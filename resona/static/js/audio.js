(() => {
  const midiToFrequency = midi => 440 * Math.pow(2, (midi - 69) / 12);
  const frequencyToMidi = frequency => 69 + 12 * Math.log2(frequency / 440);
  const softTarget = (parameter, value, now, duration = 1.5) => {
    parameter.cancelScheduledValues(now);
    parameter.setTargetAtTime(value, now, Math.max(.02, duration / 4));
  };
  class ResonaAudio {
    constructor() {
      this.context = null;
      this.playing = false;
      this.ambientPlaying = false;
      this.noisePlaying = false;
      this.chordProgressionPlaying = false;
      this.chordProgressionPaused = false;
      this.chordProgressionIndex = 0;
      this.progressionTonic = 0;
      this.chordTimer = null;
      this.nodes = [];
      this.ambientNodes = [];
      this.ambientSources = [];
      this.ambientTimers = [];
      this.ambientShutdownToken = 0;
      this.harmonyStep = 0;
      this.noiseNodes = [];
      this.config = { carrier: midiToFrequency(53), beat: 6, binauralMode:'difference', leftFrequency:midiToFrequency(53) - 3, rightFrequency:midiToFrequency(53) + 3, noise: 'pink', master: 70, mode: 'meditation', volumes: { binaural: 50, ambient: 50, noise: 50 }, layers: { pad: .72, flute: .38, strings: .51, bells: .24 }, ambient: { droneFrequency:midiToFrequency(53), manualRootMidi:53, atmosphere:'deep', tonalSource:'manual', parameters:{ warmth:80, movement:25, space:82, texture:20, shimmer:21, output:58 }, drone:58, pads:68, textures:30, melody:18, spatial:58, chordProgression:[], chordDuration:4, chordTransition:0, binauralChordTransition:0 } };
      if (window.ResonaCustomSynth && typeof window.ResonaCustomSynth.configure === 'function') window.ResonaCustomSynth.configure(this);
      this.config.ambient.droneFrequency = Math.max(40, Math.min(400, Number(this.config.ambient.droneFrequency) || 200));
      this.config.ambient.manualRootMidi = Number.isFinite(Number(this.config.ambient.manualRootMidi)) ? Number(this.config.ambient.manualRootMidi) : frequencyToMidi(this.config.ambient.droneFrequency);
      this.config.carrier = this.config.ambient.droneFrequency;
    }
    ensureContext() {
      if (!this.context) this.context = new (window.AudioContext || window.webkitAudioContext)();
      if (this.context.state === 'suspended') this.context.resume();
      return this.context;
    }
    createNoise(ctx, type) {
      const seconds = 3, length = ctx.sampleRate * seconds, buffer = ctx.createBuffer(1, length, ctx.sampleRate), data = buffer.getChannelData(0);
      let last = 0;
      for (let i = 0; i < length; i++) {
        const white = Math.random() * 2 - 1;
        if (type === 'brown' || type === 'ocean') { last = (last + (type === 'ocean' ? .008 : .02) * white) / (type === 'ocean' ? 1.008 : 1.02); data[i] = last * (type === 'ocean' ? 5 : 3.5); }
        else if (type === 'pink' || type === 'rain' || type === 'forest') { last = .97 * last + .03 * white; const detail = type === 'rain' && Math.random() > .997 ? white * 1.6 : type === 'forest' ? Math.sin(i / 37) * .05 : 0; data[i] = (white * .35 + last * 1.8 + detail) * .5; }
        else data[i] = white * .45;
      }
      const source = ctx.createBufferSource(); source.buffer = buffer; source.loop = true; return source;
    }
    start() {
      if (this.playing) return;
      const ctx = this.ensureContext(), master = ctx.createGain(); master.gain.setValueAtTime(.0001, ctx.currentTime); master.gain.exponentialRampToValueAtTime(Math.max(.0001, .5 * this.config.master / 100 * this.config.volumes.binaural / 50), ctx.currentTime + 1.4); master.connect(ctx.destination);
      const left = ctx.createOscillator(), right = ctx.createOscillator(), merger = ctx.createChannelMerger(2), lGain = ctx.createGain(), rGain = ctx.createGain();
      const pair = this.binauralPair();
      left.type = right.type = 'sine'; left.frequency.value = pair.left; right.frequency.value = pair.right; lGain.gain.value = rGain.gain.value = .16; left.connect(lGain).connect(merger, 0, 0); right.connect(rGain).connect(merger, 0, 1); merger.connect(master); left.start(); right.start();
      const toneCenter = this.binauralToneCenter(), tones = [[toneCenter / 2, 'sine', .045], [toneCenter * 1.5, 'triangle', .018], [toneCenter * 2, 'sine', .009]], binauralTones = [];
      tones.forEach(([frequency, type, gainValue], index) => { const osc = ctx.createOscillator(), gain = ctx.createGain(); osc.type = type; osc.frequency.value = frequency; gain.gain.value = gainValue * Object.values(this.config.layers)[index] || gainValue; osc.connect(gain).connect(master); osc.start(); binauralTones.push(osc); this.nodes.push(osc, gain); });
      this.nodes.push(left, right, lGain, rGain, merger, master); this.master = master; this.left = left; this.right = right; this.binauralTones = binauralTones; this.leftGain = lGain; this.rightGain = rGain; this.playing = true;
    }
    stop() { if (!this.playing) return; const now = this.context.currentTime; this.master.gain.cancelScheduledValues(now); this.master.gain.setValueAtTime(Math.max(this.master.gain.value, .0001), now); this.master.gain.exponentialRampToValueAtTime(.0001, now + .5); const nodes = this.nodes.slice(); setTimeout(() => nodes.forEach(node => { try { if (node.stop) node.stop(); else node.disconnect(); } catch (_) {} }), 600); this.nodes = []; this.binauralTones = null; this.playing = false; }
    toggle() { this.playing ? this.stop() : this.start(); return this.playing; }
    binauralPair(center = this.config.carrier, beat = this.config.beat) { return this.config.binauralMode === 'individual' ? { left:this.config.leftFrequency, right:this.config.rightFrequency } : { left:center - beat / 2, right:center + beat / 2 }; }
    binauralToneCenter() { const pair = this.binauralPair(); return (pair.left + pair.right) / 2; }
    updateBinauralFrequencies(chordTransition = null) {
      if (!this.playing) return;
      const pair = this.binauralPair(), now = this.context.currentTime;
      const retune = (parameter, frequency, glide) => {
        parameter.cancelScheduledValues(now);
        if (chordTransition === null) parameter.setTargetAtTime(frequency, now, glide);
        else if (chordTransition <= 0) parameter.setValueAtTime(frequency, now);
        else { parameter.setValueAtTime(parameter.value, now); parameter.linearRampToValueAtTime(frequency, now + chordTransition); }
      };
      retune(this.left.frequency, pair.left, .3); retune(this.right.frequency, pair.right, .3);
      const center = this.binauralToneCenter(); [center / 2, center * 1.5, center * 2].forEach((frequency, index) => retune(this.binauralTones[index].frequency, frequency, .35));
    }
    setBinauralMode(mode) {
      if (!['individual','difference'].includes(mode)) return;
      if (mode === 'individual' && this.config.binauralMode !== 'individual') { const pair = { left:this.config.carrier - this.config.beat / 2, right:this.config.carrier + this.config.beat / 2 }; this.config.leftFrequency = pair.left; this.config.rightFrequency = pair.right; }
      this.config.binauralMode = mode; this.updateBinauralFrequencies();
    }
    setEarFrequency(ear, value) {
      if (!['left','right'].includes(ear)) return;
      if (this.config.binauralMode !== 'individual') this.setBinauralMode('individual');
      this.config[ear === 'left' ? 'leftFrequency' : 'rightFrequency'] = Math.max(40, Math.min(400, Number(value)));
      this.updateBinauralFrequencies();
    }
    setBeat(value) { this.config.binauralMode = 'difference'; this.config.beat = Math.max(.1, Math.min(Number(value), 100, this.config.carrier * 1.8)); this.updateBinauralFrequencies(); }
    setChordTransition(value) { this.config.ambient.chordTransition = Math.max(0, Math.min(4, Number(value) || 0)); }
    setBinauralChordTransition(value) { this.config.ambient.binauralChordTransition = Math.max(0, Math.min(4, Number(value) || 0)); }
    setLayer(name, value) { if (name in this.config.layers) this.config.layers[name] = Number(value) / 100; }
    setVolume(name, value) { if (!(name in this.config.volumes)) return; const level = Math.max(0, Math.min(100, Number(value))); this.config.volumes[name] = level; this.applyOutputLevels(); }
    setMaster(value) { this.config.master = Math.max(0, Math.min(100, Number(value))); this.applyOutputLevels(); this.notifyState(); }
    applyOutputLevels() {
      const now = this.context?.currentTime || 0, scale = this.config.master / 100;
      if (this.playing) this.master.gain.setTargetAtTime(.5 * scale * this.config.volumes.binaural / 50, now, .04);
      if (this.ambientPlaying) this.ambientOutput.gain.setTargetAtTime(.6 * scale * this.config.volumes.ambient / 50, now, .08);
      if (this.noisePlaying) { const noiseLevel = .286 * scale * this.config.volumes.noise / 50; this.noiseOutput.gain.setTargetAtTime(noiseLevel, now, .06); this.noiseLfoDepth.gain.setTargetAtTime(noiseLevel * this.noiseLfoRatio, now, .08); }
    }
    setNoise(name) { this.config.noise = name; if (this.noisePlaying) { this.stopNoise(); setTimeout(() => this.startNoise(), 850); } }
    startNoise() {
      if (this.noisePlaying) return;
      const ctx = this.ensureContext(), source = this.createNoise(ctx, this.config.noise), filter = ctx.createBiquadFilter(), output = ctx.createGain(), lfo = ctx.createOscillator(), lfoDepth = ctx.createGain();
      const settings = { white:['allpass',1200,.01], pink:['lowpass',3200,.03], brown:['lowpass',900,.025], rain:['highpass',900,.12], ocean:['lowpass',650,.075], forest:['bandpass',1750,.055] }[this.config.noise] || ['allpass',1200,.01];
      filter.type = settings[0]; filter.frequency.value = settings[1]; filter.Q.value = this.config.noise === 'forest' ? .55 : .2;
      const noiseLevel = .286 * this.config.master / 100 * this.config.volumes.noise / 50, lfoRatio = this.config.noise === 'ocean' ? .32 : this.config.noise === 'rain' ? .12 : .06;
      output.gain.setValueAtTime(.0001, ctx.currentTime); output.gain.exponentialRampToValueAtTime(Math.max(.0001, noiseLevel), ctx.currentTime + 1.2); output.connect(ctx.destination);
      lfo.frequency.value = settings[2]; lfoDepth.gain.value = noiseLevel * lfoRatio; lfo.connect(lfoDepth).connect(output.gain); source.connect(filter).connect(output); source.start(); lfo.start();
      this.noiseNodes = [source, filter, lfo, lfoDepth, output]; this.noiseOutput = output; this.noiseLfoDepth = lfoDepth; this.noiseLfoRatio = lfoRatio; this.noisePlaying = true;
    }
    stopNoise() { if (!this.noisePlaying) return; const now = this.context.currentTime, nodes = this.noiseNodes.slice(); this.noiseOutput.gain.cancelScheduledValues(now); this.noiseOutput.gain.setValueAtTime(Math.max(this.noiseOutput.gain.value, .0001), now); this.noiseOutput.gain.exponentialRampToValueAtTime(.0001, now + .65); setTimeout(() => nodes.forEach(node => { try { if (node.stop) node.stop(); else node.disconnect(); } catch (_) {} }), 750); this.noiseNodes = []; this.noisePlaying = false; }
    toggleNoise() { this.noisePlaying ? this.stopNoise() : this.startNoise(); return this.noisePlaying; }
    atmospherePresets() {
      return {
        restore:{ warmth:72, movement:50, space:62, texture:24, shimmer:46 },
        melancholy:{ warmth:64, movement:38, space:74, texture:31, shimmer:35 },
        deep:{ warmth:80, movement:25, space:82, texture:20, shimmer:21 }
      };
    }
    setAtmosphere(name) {
      const preset = this.atmospherePresets()[name]; if (!preset) return;
      this.config.ambient.atmosphere = name;
      Object.entries(preset).forEach(([parameter, value]) => this.setAmbientParameter(parameter, value));
      if (this.ambientPlaying && this.ambientGains?.drone) softTarget(this.ambientGains.drone.gain, (name === 'deep' ? .23 : .17) * this.legacyScale('drone'), this.context.currentTime);
      this.harmonyStep = 0;
      if (this.config.ambient.tonalSource === 'manual') {
        this.scheduleManualHarmony();
        this.morphManualHarmony();
      }
    }
    setAmbientParameter(name, value) {
      if (!['warmth','movement','space','texture','shimmer','output'].includes(name)) return;
      const level = Math.max(0, Math.min(100, Number(value)));
      this.config.ambient.parameters[name] = level;
      if (!this.ambientPlaying || !this.context) return;
      const normalized = level / 100, now = this.context.currentTime;
      if (name === 'warmth' && this.ambientBodyFilter) softTarget(this.ambientBodyFilter.frequency, 720 + normalized * 1500, now, 2.5);
      if (name === 'texture' && this.ambientGains?.textures) softTarget(this.ambientGains.textures.gain, this.textureLevel(), now);
      if (name === 'shimmer' && this.ambientGains?.shimmer) softTarget(this.ambientGains.shimmer.gain, this.shimmerLevel(), now);
      if (name === 'space') {
        if (this.ambientReverbWet) softTarget(this.ambientReverbWet.gain, .24 + normalized * .48, now);
        if (this.ambientDelayWet) softTarget(this.ambientDelayWet.gain, .04 + normalized * .15, now);
      }
      if (name === 'output' && this.ambientSynthMaster) softTarget(this.ambientSynthMaster.gain, this.synthOutputLevel(), now, .7);
    }
    setTonalCentre(value) {
      const midi = Number(value); if (![48,50,51,53,55,57].includes(midi)) return;
      this.config.ambient.manualRootMidi = midi;
      this.config.ambient.droneFrequency = midiToFrequency(midi);
      if (this.config.ambient.tonalSource !== 'manual') return;
      this.config.carrier = this.config.ambient.droneFrequency;
      this.updateBinauralFrequencies();
      this.morphManualHarmony();
    }
    setDroneFrequency(value) {
      const root = Math.max(40, Math.min(400, Number(value)));
      this.config.ambient.droneFrequency = root;
      this.config.ambient.manualRootMidi = frequencyToMidi(root);
      if (this.config.ambient.tonalSource !== 'manual') return;
      this.config.carrier = root;
      this.updateBinauralFrequencies();
      this.morphManualHarmony();
    }
    setTonalSource(source) {
      if (!['manual','generated'].includes(source)) return false;
      if (source === 'generated' && !this.config.ambient.chordProgression.length) return false;
      this.config.ambient.tonalSource = source;
      clearInterval(this.chordTimer); this.chordTimer = null;
      this.clearManualHarmonyTimer();
      this.chordProgressionIndex = 0;
      if (source === 'manual') {
        this.chordProgressionPlaying = false; this.chordProgressionPaused = false;
        this.config.carrier = this.config.ambient.droneFrequency;
        this.updateBinauralFrequencies();
        if (this.ambientPlaying) { this.morphManualHarmony(); this.scheduleManualHarmony(); }
      } else {
        this.chordProgressionPlaying = true; this.chordProgressionPaused = false;
        if (!this.ambientPlaying) this.startAmbient(); else this.scheduleProgression();
      }
      this.notifyState(); return true;
    }
    parseChord(chord) {
      const noteOffsets = { C:0, Cs:1, Db:1, D:2, Ds:3, Eb:3, E:4, F:5, Fs:6, Gb:6, G:7, Gs:8, Ab:8, A:9, As:10, Bb:10, B:11 };
      const name = String(chord || 'C').trim().replace(/([A-G])#/g, '$1s').split('/')[0];
      const rootName = Object.keys(noteOffsets).sort((a,b) => b.length-a.length).find(note => name.startsWith(note)) || 'C';
      return { root:noteOffsets[rootName], quality:name.slice(rootName.length).toLowerCase() };
    }
    harmonizeProgression(chords) {
      const source = Array.isArray(chords) ? chords.slice(0,64).map(String) : [];
      if (!source.length) return [];
      const tonic = this.parseChord(source[0]), minor = tonic.quality.includes('min') && !tonic.quality.includes('maj');
      const degrees = minor ? [0,2,3,5,7,8,10] : [0,2,4,5,7,9,11];
      const qualities = minor ? ['min','dim','','min','min','',''] : ['','min','min','','','min','dim'];
      const names = ['C','Cs','D','Ds','E','F','Fs','G','Gs','A','As','B'];
      this.progressionTonic = tonic.root;
      return source.map(chord => {
        const relative = (this.parseChord(chord).root - tonic.root + 12) % 12;
        const degreeIndex = degrees.reduce((best, degree, index) => {
          const distance = Math.min(Math.abs(relative - degree), 12 - Math.abs(relative - degree));
          return distance < best.distance ? { index, distance } : best;
        }, { index:0, distance:Infinity }).index;
        return names[(tonic.root + degrees[degreeIndex]) % 12] + qualities[degreeIndex];
      });
    }
    chordIntervals(chord) {
      const parsed = this.parseChord(chord), root = (parsed.root - this.progressionTonic + 12) % 12;
      const triad = parsed.quality.includes('dim') ? [0,3,6] : parsed.quality.includes('min') && !parsed.quality.includes('maj') ? [0,3,7] : [0,4,7];
      return { root, chord:triad.map(interval => root + interval) };
    }
    retune(parameter, frequency, now, transition) {
      parameter.cancelScheduledValues(now);
      if (transition <= 0) parameter.setValueAtTime(frequency, now);
      else { parameter.setValueAtTime(parameter.value, now); parameter.linearRampToValueAtTime(frequency, now + transition); }
    }
    applyHarmony(intervals, transition, generated) {
      if (!this.ambientPlaying || !this.ambientVoices || !this.context) return;
      const now = this.context.currentTime, rootMidi = this.config.ambient.manualRootMidi;
      this.activeHarmonyIntervals = intervals.chord.slice();
      this.ambientVoices.body.forEach((voice, index) => {
        const interval = index < 6 ? intervals.chord[Math.floor(index / 2)] : intervals.chord[index - 6] - 12;
        this.retune(voice.oscillator.frequency, midiToFrequency(rootMidi + interval), now, transition);
      });
      this.ambientVoices.shimmer.forEach((voice, index) => this.retune(voice.oscillator.frequency, midiToFrequency(rootMidi + intervals.chord[index] + 12), now, transition));
      const droneRoot = generated ? intervals.root : 0;
      this.ambientVoices.drone.forEach((voice, index) => this.retune(voice.oscillator.frequency, midiToFrequency(rootMidi + droneRoot + [-12,0,12][index]), now, transition));
    }
    applyProgressionChord() {
      const chords = this.config.ambient.chordProgression;
      if (!this.ambientPlaying || this.config.ambient.tonalSource !== 'generated' || !chords.length) return;
      const intervals = this.chordIntervals(chords[this.chordProgressionIndex]);
      const chordTransition = Math.min(this.config.ambient.chordTransition, this.config.ambient.chordDuration);
      this.applyHarmony(intervals, chordTransition, true);
      this.config.carrier = midiToFrequency(this.config.ambient.manualRootMidi + intervals.root);
      this.updateBinauralFrequencies(Math.min(this.config.ambient.binauralChordTransition, this.config.ambient.chordDuration));
      this.notifyState();
    }
    scheduleProgression() {
      clearInterval(this.chordTimer); this.chordTimer = null;
      if (this.config.ambient.tonalSource !== 'generated' || !this.chordProgressionPlaying || this.chordProgressionPaused || !this.config.ambient.chordProgression.length) return;
      this.applyProgressionChord();
      this.chordTimer = setInterval(() => {
        const activeChords = this.config.ambient.chordProgression;
        if (this.chordProgressionIndex + 1 >= activeChords.length) {
          window.dispatchEvent(new CustomEvent('resona:chord-set-ended', { detail:{ chords:activeChords.slice() } }));
          if (this.config.ambient.chordProgression !== activeChords) return;
          this.chordProgressionIndex = 0;
        } else this.chordProgressionIndex += 1;
        this.applyProgressionChord();
      }, this.config.ambient.chordDuration * 1000);
    }
    setChordProgression(chords, duration) {
      this.config.ambient.chordProgression = this.harmonizeProgression(chords);
      this.config.ambient.chordDuration = Math.max(2, Math.min(120, Number(duration) || 4));
      if (!this.config.ambient.chordProgression.length) return;
      this.config.ambient.tonalSource = 'generated'; this.clearManualHarmonyTimer();
      this.chordProgressionIndex = 0; this.chordProgressionPlaying = Boolean(this.config.ambient.chordProgression.length); this.chordProgressionPaused = false;
      if (!this.ambientPlaying && this.chordProgressionPlaying) this.startAmbient(); else this.scheduleProgression();
    }
    toggleChordProgression() {
      if (!this.config.ambient.chordProgression.length) return;
      if (this.config.ambient.tonalSource !== 'generated') { this.setTonalSource('generated'); return; }
      if (!this.ambientPlaying) this.startAmbient();
      if (!this.chordProgressionPlaying) { this.chordProgressionPlaying = true; this.chordProgressionPaused = false; }
      else this.chordProgressionPaused = !this.chordProgressionPaused;
      this.scheduleProgression(); this.notifyState();
    }
    stopChordProgression() { clearInterval(this.chordTimer); this.chordTimer = null; this.chordProgressionPlaying = false; this.chordProgressionPaused = false; this.chordProgressionIndex = 0; this.notifyState(); }
    replayChordProgression() { if (!this.config.ambient.chordProgression.length) return; this.config.ambient.tonalSource = 'generated'; this.clearManualHarmonyTimer(); this.chordProgressionIndex = 0; this.chordProgressionPlaying = true; this.chordProgressionPaused = false; if (!this.ambientPlaying) this.startAmbient(); else this.scheduleProgression(); this.notifyState(); }
    notifyState() { window.dispatchEvent(new CustomEvent('resona:audio-state-change')); }
    synthOutputLevel() { return .08 + this.config.ambient.parameters.output / 100 * .24; }
    legacyScale(name) { return .4 + Math.max(0, Math.min(100, Number(this.config.ambient[name]) || 0)) / 100 * 1.2; }
    textureLevel() { return (.008 + this.config.ambient.parameters.texture / 100 * .052) * this.legacyScale('textures'); }
    shimmerLevel() { return (.006 + this.config.ambient.parameters.shimmer / 100 * .05) * this.legacyScale('melody'); }
    makeSaturationCurve(drive) {
      const samples = 4096, curve = new Float32Array(samples);
      for (let index = 0; index < samples; index += 1) { const x = index * 2 / samples - 1; curve[index] = Math.tanh(x * (1 + drive * .13)) / Math.tanh(1 + drive * .13); }
      return curve;
    }
    makeAmbientNoise(seconds) {
      const ctx = this.context, length = ctx.sampleRate * seconds, buffer = ctx.createBuffer(2, length, ctx.sampleRate);
      for (let channel = 0; channel < 2; channel += 1) {
        const data = buffer.getChannelData(channel); let brown = 0;
        for (let index = 0; index < length; index += 1) { const white = Math.random() * 2 - 1; brown = (brown + .018 * white) / 1.018; const swell = .45 + .35 * Math.sin(index / ctx.sampleRate * (.17 + channel * .023)); data[index] = brown * 3.2 * swell + white * .025; }
      }
      return buffer;
    }
    makeImpulse(seconds, decay) {
      const ctx = this.context, length = ctx.sampleRate * seconds, impulse = ctx.createBuffer(2, length, ctx.sampleRate);
      for (let channel = 0; channel < 2; channel += 1) { const data = impulse.getChannelData(channel); for (let index = 0; index < length; index += 1) data[index] = (Math.random() * 2 - 1) * Math.pow(1 - index / length, decay) * (.75 + Math.random() * .25); }
      return impulse;
    }
    rememberAmbientNode(...nodes) { this.ambientNodes.push(...nodes.filter(Boolean)); }
    rememberAmbientSource(source) { this.ambientSources.push(source); return source; }
    createAmbientVoice(midi, type, destination, level, detune) {
      const ctx = this.context, oscillator = ctx.createOscillator(), gain = ctx.createGain();
      oscillator.type = type; oscillator.frequency.value = midiToFrequency(midi); oscillator.detune.value = detune; gain.gain.value = level;
      oscillator.connect(gain).connect(destination); oscillator.start(); this.rememberAmbientSource(oscillator); this.rememberAmbientNode(gain);
      return { oscillator, gain, baseDetune:detune };
    }
    buildAmbientGraph() {
      const ctx = this.context, input = ctx.createGain(), mud = ctx.createBiquadFilter(), air = ctx.createBiquadFilter(), saturation = ctx.createWaveShaper();
      mud.type = 'peaking'; mud.frequency.value = 320; mud.Q.value = .8; mud.gain.value = -3;
      air.type = 'highshelf'; air.frequency.value = 8000; air.gain.value = 2;
      saturation.curve = this.makeSaturationCurve(7); saturation.oversample = '4x';
      const chorusIn = ctx.createGain(), chorusDry = ctx.createGain(), chorusWet = ctx.createGain(), chorusDelayL = ctx.createDelay(.05), chorusDelayR = ctx.createDelay(.05);
      chorusDry.gain.value = .74; chorusWet.gain.value = .26; chorusDelayL.delayTime.value = .018; chorusDelayR.delayTime.value = .024;
      const panL = ctx.createStereoPanner(), panR = ctx.createStereoPanner(), chorusLfo = ctx.createOscillator(), chorusDepthL = ctx.createGain(), chorusDepthR = ctx.createGain();
      panL.pan.value = -.72; panR.pan.value = .72; chorusLfo.frequency.value = .17; chorusDepthL.gain.value = .004; chorusDepthR.gain.value = -.0035;
      chorusLfo.connect(chorusDepthL).connect(chorusDelayL.delayTime); chorusLfo.connect(chorusDepthR).connect(chorusDelayR.delayTime); chorusLfo.start(); this.rememberAmbientSource(chorusLfo);
      const compressor = ctx.createDynamicsCompressor(); compressor.threshold.value = -22; compressor.knee.value = 16; compressor.ratio.value = 2.4; compressor.attack.value = .08; compressor.release.value = 1.2;
      const reverbDry = ctx.createGain(), preDelay = ctx.createDelay(.12), convolver = ctx.createConvolver(), fxSum = ctx.createGain();
      reverbDry.gain.value = .62; preDelay.delayTime.value = .055; convolver.buffer = this.makeImpulse(9.5, 2.7);
      this.ambientReverbWet = ctx.createGain(); this.ambientReverbWet.gain.value = .24 + this.config.ambient.parameters.space / 100 * .48;
      const delay = ctx.createDelay(2), feedback = ctx.createGain(), delayFilter = ctx.createBiquadFilter(), delayDry = ctx.createGain(), limiter = ctx.createDynamicsCompressor();
      delay.delayTime.value = .74; feedback.gain.value = .22; delayFilter.type = 'lowpass'; delayFilter.frequency.value = 2600; delayDry.gain.value = .88;
      this.ambientDelayWet = ctx.createGain(); this.ambientDelayWet.gain.value = .04 + this.config.ambient.parameters.space / 100 * .15;
      limiter.threshold.value = -4; limiter.knee.value = 2; limiter.ratio.value = 18; limiter.attack.value = .003; limiter.release.value = .18;
      this.ambientSynthMaster = ctx.createGain(); this.ambientAnalyser = ctx.createAnalyser(); this.ambientAnalyser.fftSize = 512; this.ambientAnalyser.smoothingTimeConstant = .9;
      this.ambientOutput = ctx.createGain(); this.ambientOutput.gain.value = .0001; this.ambientOutput.connect(ctx.destination);
      input.connect(mud).connect(air).connect(saturation).connect(chorusIn);
      chorusIn.connect(chorusDry).connect(compressor); chorusIn.connect(chorusDelayL).connect(panL).connect(chorusWet); chorusIn.connect(chorusDelayR).connect(panR).connect(chorusWet); chorusWet.connect(compressor);
      compressor.connect(reverbDry).connect(fxSum); compressor.connect(preDelay).connect(convolver).connect(this.ambientReverbWet).connect(fxSum);
      fxSum.connect(delayDry).connect(limiter); fxSum.connect(delay).connect(delayFilter).connect(this.ambientDelayWet).connect(limiter); delayFilter.connect(feedback).connect(delay);
      limiter.connect(this.ambientSynthMaster).connect(this.ambientAnalyser).connect(this.ambientOutput);
      this.ambientInput = input;
      this.rememberAmbientNode(input,mud,air,saturation,chorusIn,chorusDry,chorusWet,chorusDelayL,chorusDelayR,panL,panR,chorusDepthL,chorusDepthR,compressor,reverbDry,preDelay,convolver,this.ambientReverbWet,fxSum,delay,feedback,delayFilter,this.ambientDelayWet,delayDry,limiter,this.ambientSynthMaster,this.ambientAnalyser,this.ambientOutput);
    }
    buildAmbientLayers() {
      const ctx = this.context, root = this.config.ambient.manualRootMidi;
      const droneGain = ctx.createGain(), droneFilter = ctx.createBiquadFilter(); droneGain.gain.value = (this.config.ambient.atmosphere === 'deep' ? .23 : .17) * this.legacyScale('drone'); droneFilter.type = 'lowpass'; droneFilter.frequency.value = 430; droneFilter.Q.value = .45; droneGain.connect(droneFilter).connect(this.ambientInput);
      const drone = [-12,0,12].map((interval,index) => this.createAmbientVoice(root + interval, index === 1 ? 'triangle' : 'sine', droneGain, index === 1 ? .35 : .22, 0));
      const bodyGain = ctx.createGain(); bodyGain.gain.value = .22 * this.legacyScale('pads'); this.ambientBodyFilter = ctx.createBiquadFilter(); this.ambientBodyFilter.type = 'lowpass'; this.ambientBodyFilter.frequency.value = 720 + this.config.ambient.parameters.warmth / 100 * 1500; this.ambientBodyFilter.Q.value = .55; bodyGain.connect(this.ambientBodyFilter).connect(this.ambientInput);
      const body = [];
      [0,3,7].forEach((interval,index) => { const left = ctx.createStereoPanner(), right = ctx.createStereoPanner(); left.pan.value = -.18 - index * .04; right.pan.value = .18 + index * .04; body.push(this.createAmbientVoice(root + interval,'sawtooth',left,.055,-7),this.createAmbientVoice(root + interval,'sawtooth',right,.055,7)); left.connect(bodyGain); right.connect(bodyGain); this.rememberAmbientNode(left,right); });
      const analogDrift = ctx.createGain(); analogDrift.gain.value = .07; analogDrift.connect(bodyGain); [0,3,7].forEach(interval => body.push(this.createAmbientVoice(root + interval - 12,'triangle',analogDrift,.22,0)));
      const textureGain = ctx.createGain(), textureHigh = ctx.createBiquadFilter(), textureLow = ctx.createBiquadFilter(), texturePan = ctx.createStereoPanner(); textureGain.gain.value = this.textureLevel(); textureHigh.type = 'highpass'; textureHigh.frequency.value = 180; textureLow.type = 'lowpass'; textureLow.frequency.value = 5400; texturePan.pan.value = -.55; textureGain.connect(textureHigh).connect(textureLow).connect(texturePan).connect(this.ambientInput);
      const weather = ctx.createBufferSource(); weather.buffer = this.makeAmbientNoise(12); weather.loop = true; weather.connect(textureGain); weather.start(); this.rememberAmbientSource(weather);
      const shimmerGain = ctx.createGain(), shimmerFilter = ctx.createBiquadFilter(), shimmerPan = ctx.createStereoPanner(); shimmerGain.gain.value = this.shimmerLevel(); shimmerFilter.type = 'highpass'; shimmerFilter.frequency.value = 2100; shimmerPan.pan.value = .76; shimmerGain.connect(shimmerFilter).connect(shimmerPan).connect(this.ambientInput);
      const shimmer = [12,15,19].map((interval,index) => this.createAmbientVoice(root + interval,'sine',shimmerGain,.22,index * 2 - 2));
      const anchorGain = ctx.createGain(); anchorGain.gain.value = .08 * this.legacyScale('melody'); anchorGain.connect(this.ambientInput);
      const breath = ctx.createOscillator(), breathDepth = ctx.createGain(), filterLfo = ctx.createOscillator(), filterDepth = ctx.createGain(); breath.frequency.value = .075; breathDepth.gain.value = .018; breath.connect(breathDepth).connect(bodyGain.gain); filterLfo.frequency.value = .012; filterDepth.gain.value = 420; filterLfo.connect(filterDepth).connect(this.ambientBodyFilter.frequency); breath.start(); filterLfo.start(); this.rememberAmbientSource(breath); this.rememberAmbientSource(filterLfo);
      this.ambientVoices = { drone, body, shimmer }; this.ambientGains = { drone:droneGain, body:bodyGain, textures:textureGain, shimmer:shimmerGain, anchor:anchorGain }; this.activeHarmonyIntervals = [0,3,7];
      this.rememberAmbientNode(droneGain,droneFilter,bodyGain,this.ambientBodyFilter,analogDrift,textureGain,textureHigh,textureLow,texturePan,shimmerGain,shimmerFilter,shimmerPan,anchorGain,breathDepth,filterDepth);
    }
    clearManualHarmonyTimer() {
      if (!this.manualHarmonyTimer) return;
      clearInterval(this.manualHarmonyTimer); this.ambientTimers = this.ambientTimers.filter(timer => timer !== this.manualHarmonyTimer); this.manualHarmonyTimer = null;
    }
    scheduleManualHarmony() {
      this.clearManualHarmonyTimer();
      if (!this.ambientPlaying || this.config.ambient.tonalSource !== 'manual') return;
      const duration = this.config.ambient.atmosphere === 'deep' ? 30000 : 22000;
      this.manualHarmonyTimer = setInterval(() => { this.harmonyStep += 1; this.morphManualHarmony(); }, duration); this.ambientTimers.push(this.manualHarmonyTimer);
    }
    morphManualHarmony() {
      if (!this.ambientPlaying || this.config.ambient.tonalSource !== 'manual') return;
      const progressions = { restore:[[0,4,7],[5,9,12],[7,11,14],[5,9,12]], melancholy:[[0,3,7],[-2,2,5],[-4,0,3],[-5,-2,2]], deep:[[0,3,7],[0,5,7],[-2,3,7],[0,3,8]] };
      const chords = progressions[this.config.ambient.atmosphere], chord = chords[this.harmonyStep % chords.length];
      this.applyHarmony({ root:0, chord }, 5, false);
    }
    applyRandomDrift() {
      if (!this.ambientPlaying || !this.context || !this.ambientVoices) return;
      const now = this.context.currentTime, amount = 1.5 + this.config.ambient.parameters.movement / 100 * 6;
      [...this.ambientVoices.body,...this.ambientVoices.shimmer].forEach(voice => softTarget(voice.oscillator.detune, voice.baseDetune + (Math.random() - .5) * amount, now, 3.5));
      softTarget(this.ambientBodyFilter.frequency, 850 + this.config.ambient.parameters.warmth / 100 * 1100 + (Math.random() - .5) * 260 * this.config.ambient.parameters.movement / 100, now, 4);
    }
    playFeltAnchor() {
      if (!this.ambientPlaying || !this.context || !this.ambientGains?.anchor) return;
      const ctx = this.context, tones = this.activeHarmonyIntervals?.length ? this.activeHarmonyIntervals : (this.config.ambient.atmosphere === 'restore' ? [0,4,7,9] : [0,3,7,10]);
      const note = this.config.ambient.manualRootMidi + tones[Math.floor(Math.random() * tones.length)] + 12, now = ctx.currentTime;
      const oscillator = ctx.createOscillator(), overtone = ctx.createOscillator(), envelope = ctx.createGain(), soft = ctx.createBiquadFilter(), pan = ctx.createStereoPanner();
      oscillator.type = 'triangle'; oscillator.frequency.value = midiToFrequency(note); overtone.type = 'sine'; overtone.frequency.value = midiToFrequency(note) * 2.01;
      envelope.gain.setValueAtTime(.0001,now); envelope.gain.exponentialRampToValueAtTime(.12,now + .018); envelope.gain.exponentialRampToValueAtTime(.0001,now + 7.5); soft.type = 'lowpass'; soft.frequency.value = 1800; pan.pan.value = (Math.random() - .5) * 1.2;
      oscillator.connect(envelope); overtone.connect(envelope); envelope.connect(soft).connect(pan).connect(this.ambientGains.anchor); oscillator.start(now); overtone.start(now); oscillator.stop(now + 8); overtone.stop(now + 8); this.rememberAmbientSource(oscillator); this.rememberAmbientSource(overtone); this.rememberAmbientNode(envelope,soft,pan);
    }
    scheduleAmbientEvolution() {
      this.ambientTimers.push(setInterval(() => this.applyRandomDrift(),4300));
      this.ambientTimers.push(setInterval(() => this.playFeltAnchor(),14000 + Math.random() * 7000));
      this.scheduleManualHarmony();
    }
    startAmbient() {
      if (this.ambientPlaying) return;
      const ctx = this.ensureContext(); this.ambientShutdownToken += 1; this.ambientNodes = []; this.ambientSources = []; this.ambientTimers = [];
      this.buildAmbientGraph(); this.buildAmbientLayers(); this.ambientPlaying = true; this.scheduleAmbientEvolution();
      const now = ctx.currentTime, overall = Math.max(.0001,.6 * this.config.master / 100 * this.config.volumes.ambient / 50); this.ambientSynthMaster.gain.setValueAtTime(.0001,now); this.ambientSynthMaster.gain.exponentialRampToValueAtTime(this.synthOutputLevel(),now + 4.5); this.ambientOutput.gain.setValueAtTime(.0001,now); this.ambientOutput.gain.exponentialRampToValueAtTime(overall,now + 2.4);
      if (this.config.ambient.tonalSource === 'generated' && this.config.ambient.chordProgression.length) { this.chordProgressionPlaying = true; this.chordProgressionPaused = false; this.scheduleProgression(); } else { this.config.ambient.tonalSource = 'manual'; this.config.carrier = this.config.ambient.droneFrequency; this.morphManualHarmony(); }
    }
    stopAmbient() {
      if (!this.ambientPlaying) return;
      clearInterval(this.chordTimer); this.chordTimer = null; this.chordProgressionPlaying = false; this.ambientTimers.forEach(clearInterval); this.ambientTimers = []; this.manualHarmonyTimer = null;
      const now = this.context.currentTime, sources = this.ambientSources.slice(), nodes = this.ambientNodes.slice(), output = this.ambientOutput, token = ++this.ambientShutdownToken;
      output.gain.cancelScheduledValues(now); output.gain.setValueAtTime(Math.max(output.gain.value,.0001),now); output.gain.exponentialRampToValueAtTime(.0001,now + 2.8); this.ambientPlaying = false;
      setTimeout(() => { sources.forEach(source => { try { source.stop(); } catch (_) {} }); nodes.forEach(node => { try { node.disconnect(); } catch (_) {} }); if (this.ambientShutdownToken === token) { this.ambientNodes = []; this.ambientSources = []; this.ambientVoices = null; this.ambientAnalyser = null; } },3000);
    }
    toggleAmbient() { this.ambientPlaying ? this.stopAmbient() : this.startAmbient(); return this.ambientPlaying; }
    setAmbient(name, value) {
      if (!['drone','pads','textures','melody','spatial'].includes(name)) return;
      const level = Math.max(0,Math.min(100,Number(value))); this.config.ambient[name] = level;
      if (name === 'spatial') { this.setAmbientParameter('space',level); return; }
      if (!this.ambientPlaying || !this.ambientGains) return;
      const now = this.context.currentTime;
      if (name === 'drone') softTarget(this.ambientGains.drone.gain,(this.config.ambient.atmosphere === 'deep' ? .23 : .17) * this.legacyScale('drone'),now);
      if (name === 'pads') softTarget(this.ambientGains.body.gain,.22 * this.legacyScale('pads'),now);
      if (name === 'textures') softTarget(this.ambientGains.textures.gain,this.textureLevel(),now);
      if (name === 'melody') { softTarget(this.ambientGains.shimmer.gain,this.shimmerLevel(),now); softTarget(this.ambientGains.anchor.gain,.08 * this.legacyScale('melody'),now); }
    }
  }
  window.resonaAudio = new ResonaAudio();
})();
