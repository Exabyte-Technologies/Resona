(() => {
  class ResonaAudio {
    constructor() {
      this.context = null;
      this.playing = false;
      this.ambientPlaying = false;
      this.noisePlaying = false;
      this.nodes = [];
      this.ambientNodes = [];
      this.noiseNodes = [];
      this.config = { carrier: 216, beat: 6, noise: 'pink', master: .34, volumes: { binaural: 50, ambient: 50, noise: 50 }, layers: { pad: .72, flute: .38, strings: .51, bells: .24 }, ambient: { drone: 48, pads: 64, textures: 32, melody: 24, spatial: 46 } };
      if (window.ResonaCustomSynth && typeof window.ResonaCustomSynth.configure === 'function') window.ResonaCustomSynth.configure(this);
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
      const ctx = this.ensureContext(), master = ctx.createGain(); master.gain.setValueAtTime(.0001, ctx.currentTime); master.gain.exponentialRampToValueAtTime(Math.max(.0001, this.config.master * this.config.volumes.binaural / 50), ctx.currentTime + 1.4); master.connect(ctx.destination);
      const left = ctx.createOscillator(), right = ctx.createOscillator(), merger = ctx.createChannelMerger(2), lGain = ctx.createGain(), rGain = ctx.createGain();
      left.type = right.type = 'sine'; left.frequency.value = this.config.carrier; right.frequency.value = this.config.carrier + this.config.beat; lGain.gain.value = rGain.gain.value = .16; left.connect(lGain).connect(merger, 0, 0); right.connect(rGain).connect(merger, 0, 1); merger.connect(master); left.start(); right.start();
      const tones = [[this.config.carrier / 2, 'sine', .045], [this.config.carrier * 1.5, 'triangle', .018], [this.config.carrier * 2, 'sine', .009]];
      tones.forEach(([frequency, type, gainValue], index) => { const osc = ctx.createOscillator(), gain = ctx.createGain(); osc.type = type; osc.frequency.value = frequency; gain.gain.value = gainValue * Object.values(this.config.layers)[index] || gainValue; osc.connect(gain).connect(master); osc.start(); this.nodes.push(osc, gain); });
      this.nodes.push(left, right, lGain, rGain, merger, master); this.master = master; this.left = left; this.right = right; this.leftGain = lGain; this.rightGain = rGain; this.playing = true;
    }
    stop() { if (!this.playing) return; const now = this.context.currentTime; this.master.gain.cancelScheduledValues(now); this.master.gain.setValueAtTime(Math.max(this.master.gain.value, .0001), now); this.master.gain.exponentialRampToValueAtTime(.0001, now + .5); const nodes = this.nodes.slice(); setTimeout(() => nodes.forEach(node => { try { if (node.stop) node.stop(); else node.disconnect(); } catch (_) {} }), 600); this.nodes = []; this.playing = false; }
    toggle() { this.playing ? this.stop() : this.start(); return this.playing; }
    setBeat(value) { this.config.beat = Number(value); if (this.playing) this.right.frequency.setTargetAtTime(this.config.carrier + this.config.beat, this.context.currentTime, .3); }
    setLayer(name, value) { if (name in this.config.layers) this.config.layers[name] = Number(value) / 100; }
    setVolume(name, value) { if (!(name in this.config.volumes)) return; const level = Math.max(0, Math.min(100, Number(value))); this.config.volumes[name] = level; const now = this.context?.currentTime || 0; if (name === 'binaural' && this.playing) this.master.gain.setTargetAtTime(this.config.master * level / 50, now, .04); else if (name === 'ambient' && this.ambientPlaying) this.ambientOutput.gain.setTargetAtTime(.42 * level / 50, now, .08); else if (name === 'noise' && this.noisePlaying) { const noiseLevel = .20 * level / 50; this.noiseOutput.gain.setTargetAtTime(noiseLevel, now, .06); this.noiseLfoDepth.gain.setTargetAtTime(noiseLevel * this.noiseLfoRatio, now, .08); } }
    setNoise(name) { this.config.noise = name; if (this.noisePlaying) { this.stopNoise(); setTimeout(() => this.startNoise(), 850); } }
    startNoise() {
      if (this.noisePlaying) return;
      const ctx = this.ensureContext(), source = this.createNoise(ctx, this.config.noise), filter = ctx.createBiquadFilter(), output = ctx.createGain(), lfo = ctx.createOscillator(), lfoDepth = ctx.createGain();
      const settings = { white:['allpass',1200,.01], pink:['lowpass',3200,.03], brown:['lowpass',900,.025], rain:['highpass',900,.12], ocean:['lowpass',650,.075], forest:['bandpass',1750,.055] }[this.config.noise] || ['allpass',1200,.01];
      filter.type = settings[0]; filter.frequency.value = settings[1]; filter.Q.value = this.config.noise === 'forest' ? .55 : .2;
      const noiseLevel = .20 * this.config.volumes.noise / 50, lfoRatio = this.config.noise === 'ocean' ? .32 : this.config.noise === 'rain' ? .12 : .06;
      output.gain.setValueAtTime(.0001, ctx.currentTime); output.gain.exponentialRampToValueAtTime(Math.max(.0001, noiseLevel), ctx.currentTime + 1.2); output.connect(ctx.destination);
      lfo.frequency.value = settings[2]; lfoDepth.gain.value = noiseLevel * lfoRatio; lfo.connect(lfoDepth).connect(output.gain); source.connect(filter).connect(output); source.start(); lfo.start();
      this.noiseNodes = [source, filter, lfo, lfoDepth, output]; this.noiseOutput = output; this.noiseLfoDepth = lfoDepth; this.noiseLfoRatio = lfoRatio; this.noisePlaying = true;
    }
    stopNoise() { if (!this.noisePlaying) return; const now = this.context.currentTime, nodes = this.noiseNodes.slice(); this.noiseOutput.gain.cancelScheduledValues(now); this.noiseOutput.gain.setValueAtTime(Math.max(this.noiseOutput.gain.value, .0001), now); this.noiseOutput.gain.exponentialRampToValueAtTime(.0001, now + .65); setTimeout(() => nodes.forEach(node => { try { if (node.stop) node.stop(); else node.disconnect(); } catch (_) {} }), 750); this.noiseNodes = []; this.noisePlaying = false; }
    toggleNoise() { this.noisePlaying ? this.stopNoise() : this.startNoise(); return this.noisePlaying; }
    startAmbient() {
      if (this.ambientPlaying) return;
      const ctx = this.ensureContext(), output = ctx.createGain(), bus = ctx.createGain();
      output.gain.setValueAtTime(.0001, ctx.currentTime); output.gain.exponentialRampToValueAtTime(Math.max(.0001, .42 * this.config.volumes.ambient / 50), ctx.currentTime + 2.4); output.connect(ctx.destination);
      const panner = ctx.createStereoPanner ? ctx.createStereoPanner() : ctx.createGain(), delay = ctx.createDelay(2), wet = ctx.createGain(), feedback = ctx.createGain();
      delay.delayTime.value = .22 + this.config.ambient.spatial / 180; wet.gain.value = .28 * this.config.ambient.spatial / 100; feedback.gain.value = .13 + .22 * this.config.ambient.spatial / 100;
      bus.connect(panner); panner.connect(output); panner.connect(delay); delay.connect(wet).connect(output); delay.connect(feedback).connect(delay);
      let panLfo = null, panDepth = null;
      if (panner.pan) { panLfo = ctx.createOscillator(); panDepth = ctx.createGain(); panLfo.frequency.value = .035; panDepth.gain.value = this.config.ambient.spatial / 100; panLfo.connect(panDepth).connect(panner.pan); panLfo.start(); }

      const droneGain = ctx.createGain(); droneGain.gain.value = .10 * this.config.ambient.drone / 100; droneGain.connect(bus);
      [54, 81].forEach((frequency, index) => { const oscillator = ctx.createOscillator(); oscillator.type = index ? 'triangle' : 'sine'; oscillator.frequency.value = frequency; oscillator.detune.value = index ? -7 : 4; oscillator.connect(droneGain); oscillator.start(); this.ambientNodes.push(oscillator); });

      const padGain = ctx.createGain(); padGain.gain.value = .035 * this.config.ambient.pads / 100; padGain.connect(bus);
      [108, 135, 162, 216].forEach((frequency, index) => { const oscillator = ctx.createOscillator(); oscillator.type = index % 2 ? 'triangle' : 'sine'; oscillator.frequency.value = frequency; oscillator.detune.value = [-9, 6, -4, 11][index]; oscillator.connect(padGain); oscillator.start(); this.ambientNodes.push(oscillator); });

      const texture = this.createNoise(ctx, 'pink'), textureFilter = ctx.createBiquadFilter(), textureGain = ctx.createGain(), textureLfo = ctx.createOscillator(), textureDepth = ctx.createGain();
      textureFilter.type = 'bandpass'; textureFilter.frequency.value = 1100; textureFilter.Q.value = .45; textureGain.gain.value = .10 * this.config.ambient.textures / 100; textureLfo.frequency.value = .08; textureDepth.gain.value = 520; textureLfo.connect(textureDepth).connect(textureFilter.frequency); texture.connect(textureFilter).connect(textureGain).connect(bus); texture.start(); textureLfo.start();

      const melodyGain = ctx.createGain(), melodyLfo = ctx.createOscillator(), melodyDepth = ctx.createGain();
      melodyGain.gain.value = .025 * this.config.ambient.melody / 100; melodyLfo.frequency.value = .07; melodyDepth.gain.value = .018 * this.config.ambient.melody / 100; melodyLfo.connect(melodyDepth).connect(melodyGain.gain); melodyLfo.start(); melodyGain.connect(bus);
      [432, 540].forEach((frequency, index) => { const oscillator = ctx.createOscillator(); oscillator.type = 'sine'; oscillator.frequency.value = frequency; oscillator.detune.value = index ? 7 : -5; oscillator.connect(melodyGain); oscillator.start(); this.ambientNodes.push(oscillator); });

      this.ambientNodes.push(texture, textureFilter, textureGain, textureLfo, textureDepth, melodyLfo, melodyDepth, melodyGain, droneGain, padGain, bus, panner, delay, wet, feedback, output); if (panLfo) this.ambientNodes.push(panLfo, panDepth);
      this.ambientOutput = output; this.ambientGains = { drone:droneGain, pads:padGain, textures:textureGain, melody:melodyGain }; this.ambientSpatial = { delay, wet, feedback, panDepth }; this.ambientPlaying = true;
    }
    stopAmbient() { if (!this.ambientPlaying) return; const now = this.context.currentTime, nodes = this.ambientNodes.slice(); this.ambientOutput.gain.cancelScheduledValues(now); this.ambientOutput.gain.setValueAtTime(Math.max(this.ambientOutput.gain.value, .0001), now); this.ambientOutput.gain.exponentialRampToValueAtTime(.0001, now + 1); setTimeout(() => nodes.forEach(node => { try { if (node.stop) node.stop(); else node.disconnect(); } catch (_) {} }), 1100); this.ambientNodes = []; this.ambientPlaying = false; }
    toggleAmbient() { this.ambientPlaying ? this.stopAmbient() : this.startAmbient(); return this.ambientPlaying; }
    setAmbient(name, value) { if (!(name in this.config.ambient)) return; const level = Math.max(0, Math.min(100, Number(value))); this.config.ambient[name] = level; if (!this.ambientPlaying) return; const now = this.context.currentTime; const scales = { drone:.10, pads:.035, textures:.10, melody:.025 }; if (name in scales) this.ambientGains[name].gain.setTargetAtTime(scales[name] * level / 100, now, .12); else { this.ambientSpatial.delay.delayTime.setTargetAtTime(.22 + level / 180, now, .2); this.ambientSpatial.wet.gain.setTargetAtTime(.28 * level / 100, now, .2); this.ambientSpatial.feedback.gain.setTargetAtTime(.13 + .22 * level / 100, now, .2); if (this.ambientSpatial.panDepth) this.ambientSpatial.panDepth.gain.setTargetAtTime(level / 100, now, .2); } }
  }
  window.resonaAudio = new ResonaAudio();
})();
