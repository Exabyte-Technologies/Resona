(() => {
  const sigmoid = value => 1 / (1 + Math.exp(-value));

  class ResonaChordModel {
    constructor(username) {
      this.username = username;
      this.ready = false;
      this.loading = null;
    }

    async load() {
      if (this.ready) return this;
      if (this.loading) return this.loading;
      this.loading = (async () => {
        const base = `/storage/${encodeURIComponent(this.username)}/static/chord-model/`;
        const [manifestResponse, weightsResponse] = await Promise.all([fetch(base + 'model.json'), fetch(base + 'weights.bin')]);
        if (!manifestResponse.ok || !weightsResponse.ok) throw new Error('The private chord model could not be loaded');
        this.manifest = await manifestResponse.json();
        const buffer = await weightsResponse.arrayBuffer();
        this.weights = {};
        Object.entries(this.manifest.tensors).forEach(([name, tensor]) => {
          this.weights[name] = new Float32Array(buffer, tensor.offset, tensor.length);
        });
        this.vocabulary = this.manifest.vocabulary;
        this.hiddenSize = this.manifest.architecture.hiddenDimension;
        this.ready = true;
        return this;
      })();
      return this.loading;
    }

    normalizeChord(value) {
      return value.trim().replace(/([A-G])#/g, '$1s');
    }

    displayChord(value) {
      return value.replace(/([A-G])s/g, '$1♯');
    }

    embed(parts) {
      const output = new Float32Array(48);
      parts.forEach((part, partIndex) => {
        const table = this.weights[`embeddings.${partIndex}.weight`], source = part * 16;
        for (let index = 0; index < 16; index++) output[partIndex * 16 + index] = table[source + index];
      });
      return output;
    }

    runContext(chordIds) {
      const hidden = new Float32Array(this.hiddenSize), cell = new Float32Array(this.hiddenSize);
      const inputWeights = this.weights['encoder.weight_ih_l0'], hiddenWeights = this.weights['encoder.weight_hh_l0'];
      const inputBias = this.weights['encoder.bias_ih_l0'], hiddenBias = this.weights['encoder.bias_hh_l0'];
      chordIds.forEach(chordId => {
        const input = this.embed(this.vocabulary.parts[chordId]), gates = new Float32Array(this.hiddenSize * 4);
        for (let row = 0; row < gates.length; row++) {
          let value = inputBias[row] + hiddenBias[row], offset = row * 48;
          for (let column = 0; column < 48; column++) value += inputWeights[offset + column] * input[column];
          offset = row * this.hiddenSize;
          for (let column = 0; column < this.hiddenSize; column++) value += hiddenWeights[offset + column] * hidden[column];
          gates[row] = value;
        }
        for (let index = 0; index < this.hiddenSize; index++) {
          const inputGate = sigmoid(gates[index]), forgetGate = sigmoid(gates[this.hiddenSize + index]);
          const candidate = Math.tanh(gates[this.hiddenSize * 2 + index]), outputGate = sigmoid(gates[this.hiddenSize * 3 + index]);
          cell[index] = forgetGate * cell[index] + inputGate * candidate;
          hidden[index] = outputGate * Math.tanh(cell[index]);
        }
      });
      return hidden;
    }

    linear(input, weightName, biasName, rows) {
      const weights = this.weights[weightName], bias = this.weights[biasName], output = new Float32Array(rows), columns = input.length;
      for (let row = 0; row < rows; row++) {
        let value = bias[row], offset = row * columns;
        for (let column = 0; column < columns; column++) value += weights[offset + column] * input[column];
        output[row] = value;
      }
      return output;
    }

    logits(context) {
      const hidden = this.runContext(context.slice(-50));
      const root = this.linear(hidden, 'part_heads.0.weight', 'part_heads.0.bias', 18);
      const quality = this.linear(hidden, 'part_heads.1.weight', 'part_heads.1.bias', 56);
      const bass = this.linear(hidden, 'part_heads.2.weight', 'part_heads.2.bias', 18);
      const parts = new Float32Array(92); parts.set(root); parts.set(quality, 18); parts.set(bass, 74);
      return this.linear(parts, 'chord_head.weight', 'chord_head.bias', 3861);
    }

    sample(logits, temperature, topK, greedy) {
      if (greedy) {
        let best = this.vocabulary.validIds[0];
        this.vocabulary.validIds.forEach(id => { if (logits[id] > logits[best]) best = id; });
        return best;
      }
      const candidates = [];
      this.vocabulary.validIds.forEach(id => {
        const value = logits[id] / temperature;
        if (candidates.length < topK) {
          candidates.push({ id, value }); candidates.sort((a, b) => a.value - b.value);
        } else if (value > candidates[0].value) {
          candidates[0] = { id, value }; candidates.sort((a, b) => a.value - b.value);
        }
      });
      const maximum = candidates[candidates.length - 1].value;
      let total = 0;
      candidates.forEach(candidate => { candidate.weight = Math.exp(candidate.value - maximum); total += candidate.weight; });
      let choice = Math.random() * total;
      for (const candidate of candidates) { choice -= candidate.weight; if (choice <= 0) return candidate.id; }
      return candidates[candidates.length - 1].id;
    }

    async generate(options) {
      await this.load();
      const length = Math.max(1, Math.min(64, Number(options.length) || 8));
      const temperature = Math.max(.1, Math.min(2, Number(options.temperature) || 1));
      const topK = Math.max(1, Math.min(100, Number(options.topK) || 10));
      const seeds = (options.seedChords || []).map(value => this.normalizeChord(value)).filter(Boolean);
      const unknown = seeds.filter(chord => this.vocabulary.chordToId[chord] == null);
      if (unknown.length) throw new Error(`Unknown seed chord${unknown.length > 1 ? 's' : ''}: ${unknown.map(this.displayChord).join(', ')}`);
      const progression = seeds.map(chord => this.vocabulary.chordToId[chord]);
      if (!progression.length) progression.push(this.vocabulary.starterIds[Math.floor(Math.random() * this.vocabulary.starterIds.length)]);
      while (progression.length < length) progression.push(this.sample(this.logits(progression), temperature, topK, Boolean(options.greedy)));
      return progression.slice(0, length).map(id => this.vocabulary.idToChord[id]);
    }
  }

  window.ResonaChordModel = ResonaChordModel;
})();
