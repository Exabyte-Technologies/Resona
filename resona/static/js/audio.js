(() => {
  class ResonaAudio {
    constructor() {
      this.context = null;
      this.playing = false;
      this.nodes = [];
      this.config = { carrier: 216, beat: 6, noise: 'pink', master: .34, layers: { pad: .72, flute: .38, strings: .51, bells: .24 } };
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
        if (type === 'brown') { last = (last + .02 * white) / 1.02; data[i] = last * 3.5; }
        else if (type === 'pink' || type === 'rain') { last = .97 * last + .03 * white; data[i] = (white * .35 + last * 1.8) * .5; }
        else data[i] = white * .45;
      }
      const source = ctx.createBufferSource(); source.buffer = buffer; source.loop = true; return source;
    }
    start() {
      if (this.playing) return;
      const ctx = this.ensureContext(), master = ctx.createGain(); master.gain.setValueAtTime(.0001, ctx.currentTime); master.gain.exponentialRampToValueAtTime(this.config.master, ctx.currentTime + 1.4); master.connect(ctx.destination);
      const left = ctx.createOscillator(), right = ctx.createOscillator(), merger = ctx.createChannelMerger(2), lGain = ctx.createGain(), rGain = ctx.createGain();
      left.type = right.type = 'sine'; left.frequency.value = this.config.carrier; right.frequency.value = this.config.carrier + this.config.beat; lGain.gain.value = rGain.gain.value = .16; left.connect(lGain).connect(merger, 0, 0); right.connect(rGain).connect(merger, 0, 1); merger.connect(master); left.start(); right.start();
      const noise = this.createNoise(ctx, this.config.noise); const noiseGain = ctx.createGain(); noiseGain.gain.value = .10; noise.connect(noiseGain).connect(master); noise.start();
      const tones = [[this.config.carrier / 2, 'sine', .045], [this.config.carrier * 1.5, 'triangle', .018], [this.config.carrier * 2, 'sine', .009]];
      tones.forEach(([frequency, type, gainValue], index) => { const osc = ctx.createOscillator(), gain = ctx.createGain(); osc.type = type; osc.frequency.value = frequency; gain.gain.value = gainValue * Object.values(this.config.layers)[index] || gainValue; osc.connect(gain).connect(master); osc.start(); this.nodes.push(osc, gain); });
      this.nodes.push(left, right, lGain, rGain, merger, noise, noiseGain, master); this.master = master; this.left = left; this.right = right; this.playing = true;
    }
    stop() { if (!this.playing) return; const now = this.context.currentTime; this.master.gain.cancelScheduledValues(now); this.master.gain.setValueAtTime(Math.max(this.master.gain.value, .0001), now); this.master.gain.exponentialRampToValueAtTime(.0001, now + .5); const nodes = this.nodes.slice(); setTimeout(() => nodes.forEach(node => { try { if (node.stop) node.stop(); else node.disconnect(); } catch (_) {} }), 600); this.nodes = []; this.playing = false; }
    toggle() { this.playing ? this.stop() : this.start(); return this.playing; }
    setBeat(value) { this.config.beat = Number(value); if (this.playing) this.right.frequency.setTargetAtTime(this.config.carrier + this.config.beat, this.context.currentTime, .3); }
    setLayer(name, value) { if (name in this.config.layers) this.config.layers[name] = Number(value) / 100; }
    setNoise(name) { this.config.noise = name; if (this.playing) { this.stop(); setTimeout(() => this.start(), 650); } }
  }
  window.resonaAudio = new ResonaAudio();
})();
