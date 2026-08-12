#!/usr/bin/env python3
"""Export the trusted PyTorch chord checkpoint to Resona's browser format.

This deliberately uses only the Python standard library. It reads tensor metadata
from the trusted checkpoint and copies the checkpoint's raw float32 storage into
one binary file; it does not execute PyTorch or accept user-provided pickle files.
"""

import argparse
import io
import json
import pickle
import zipfile
from collections import OrderedDict
from pathlib import Path


class StorageReference:
    def __init__(self, key):
        self.key = str(key)


class TensorReference:
    def __init__(self, storage, offset, shape, stride):
        self.storage = storage
        self.offset = int(offset)
        self.shape = list(shape)
        self.stride = list(stride)


def rebuild_tensor(storage, offset, shape, stride, *_unused):
    return TensorReference(storage, offset, shape, stride)


class TrustedTorchUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if module == "collections" and name == "OrderedDict":
            return OrderedDict
        if module == "torch._utils" and name == "_rebuild_tensor_v2":
            return rebuild_tensor
        if module == "torch" and name == "FloatStorage":
            return object
        raise pickle.UnpicklingError(f"Unsupported checkpoint global: {module}.{name}")

    def persistent_load(self, persistent_id):
        kind, _storage_type, key, _device, _size = persistent_id
        if kind != "storage":
            raise pickle.UnpicklingError(f"Unsupported persistent object: {kind}")
        return StorageReference(key)


def load_checkpoint(checkpoint_path):
    with zipfile.ZipFile(checkpoint_path) as archive:
        pickle_name = next(name for name in archive.namelist() if name.endswith("/data.pkl"))
        prefix = pickle_name.rsplit("/", 1)[0]
        checkpoint = TrustedTorchUnpickler(io.BytesIO(archive.read(pickle_name))).load()
        state = checkpoint["model_state_dict"]
        storages = {
            name.rsplit("/", 1)[1]: archive.read(name)
            for name in archive.namelist()
            if name.startswith(prefix + "/data/")
        }
    return checkpoint, state, storages


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--vocabs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    checkpoint, state, storages = load_checkpoint(args.checkpoint)
    with args.vocabs.open("rb") as vocab_file:
        vocabs = pickle.load(vocab_file)

    args.output.mkdir(parents=True, exist_ok=True)
    weights_path = args.output / "weights.bin"
    manifest_tensors = {}
    byte_offset = 0
    with weights_path.open("wb") as output:
        for name, tensor in state.items():
            if not isinstance(tensor, TensorReference) or tensor.offset != 0:
                raise ValueError(f"Unsupported tensor layout for {name}")
            expected_values = 1
            for dimension in tensor.shape:
                expected_values *= dimension
            raw = storages[tensor.storage.key]
            if len(raw) != expected_values * 4:
                raise ValueError(f"Unexpected float32 storage size for {name}")
            output.write(raw)
            manifest_tensors[name] = {
                "offset": byte_offset,
                "length": expected_values,
                "shape": tensor.shape,
            }
            byte_offset += len(raw)

    chord_to_idx = vocabs["chord_to_idx"]
    id_to_chord = [None] * len(chord_to_idx)
    for chord, chord_id in chord_to_idx.items():
        id_to_chord[chord_id] = chord
    chord_parts = vocabs["chord_id_to_part_ids_3"]
    parts = [list(chord_parts[index]) for index in range(len(id_to_chord))]
    roots = set(vocabs["root_to_idx"])
    roots.discard("s")
    starter_ids = [
        chord_id for chord, chord_id in chord_to_idx.items()
        if chord_id in chord_parts and (chord in roots or (chord.endswith("min") and chord[:-3] in roots))
    ]
    manifest = {
        "format": "resona-chord-lstm-v1",
        "architecture": {
            "representation": "triad",
            "modelType": "lstm",
            "embeddingDimension": checkpoint["embed_dim"],
            "hiddenDimension": checkpoint["hidden_dim"],
            "layers": checkpoint["num_layers"],
            "partSizes": [len(vocabs["root_to_idx"]), len(vocabs["qualex_to_idx"]), len(vocabs["bass_to_idx"])],
            "vocabularySize": len(chord_to_idx),
            "maximumContext": 50,
        },
        "tensors": manifest_tensors,
        "vocabulary": {
            "idToChord": id_to_chord,
            "chordToId": chord_to_idx,
            "parts": parts,
            "validIds": sorted(chord_parts),
            "starterIds": starter_ids,
        },
    }
    (args.output / "model.json").write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
    print(f"Exported {len(manifest_tensors)} tensors and {len(id_to_chord)} chords ({byte_offset} bytes)")


if __name__ == "__main__":
    main()
