import json
import re

from .user_storage import default_home_page, delete_user_storage, initialize_user_storage, safe_path, user_root, write_user_file


DEMO_USERNAME = "demo"
DEMO_SYSTEM_PROMPT = """Resona Demo accepts three deterministic requests: create a meditation guiding page, create a motivation quotes page, or create a sleep timer. Each request installs a reviewed built-in page without contacting an AI provider or allowing arbitrary workspace edits."""
DEMO_CUSTOM_SYNTH = "window.ResonaCustomSynth = { version: 1, configure(engine) { engine.config.carrier = engine.config.ambient.droneFrequency; } };\n"

DEMO_PAGE_STYLE = """
<style>
.demo-wrap{width:min(920px,100%);margin:auto;padding:clamp(28px,7vw,76px) clamp(18px,5vw,48px) 130px}.demo-kicker{margin:0;color:#b9e68c;font-size:12px;font-weight:800;letter-spacing:.18em;text-transform:uppercase}.demo-wrap h1{max-width:760px;margin:10px 0 14px;font:600 clamp(44px,8vw,78px)/.98 Georgia,serif;letter-spacing:-.05em}.demo-lead{max-width:650px;margin:0;color:#aaa9a2;font-size:16px}.demo-card{margin-top:42px;padding:clamp(22px,5vw,42px);border:1px solid rgba(255,255,255,.1);border-radius:30px;background:linear-gradient(145deg,rgba(31,39,29,.96),rgba(18,23,18,.96));box-shadow:0 24px 70px rgba(0,0,0,.28)}.demo-action{min-height:48px;padding:12px 22px;border:0;border-radius:999px;background:#b9e68c;color:#13200d;font-weight:800}.demo-action.secondary{border:1px solid rgba(255,255,255,.12);background:#20261d;color:#f3f1e9}.demo-actions{display:flex;flex-wrap:wrap;gap:10px;justify-content:center;margin-top:24px}@media(max-width:520px){.demo-wrap{padding-top:28px}.demo-card{margin-top:30px;border-radius:23px}.demo-action{width:100%}}
</style>
"""


MEDITATION_PAGE = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="../static/user.css">{DEMO_PAGE_STYLE}<style>
.breath-stage{{display:grid;place-items:center;min-height:320px;text-align:center}}.breath-orb{{display:grid;place-items:center;width:190px;height:190px;border-radius:50%;background:radial-gradient(circle,#c9f1a3,#789e58);color:#13200d;box-shadow:0 0 0 18px rgba(185,230,140,.06),0 25px 70px #0008;transition:transform 4s ease-in-out}}.breath-orb.active{{transform:scale(1.18)}}.breath-orb strong{{font:600 30px Georgia,serif}}.breath-orb span{{display:block;margin-top:4px;font-size:12px}}.meditation-note{{margin:22px auto 0;max-width:500px;color:#92988e;text-align:center}}
</style></head><body><main class="demo-wrap"><p class="demo-kicker">Guided meditation</p><h1>Return to your breath.</h1><p class="demo-lead">A short four-part breathing practice for settling attention and releasing tension.</p><section class="demo-card"><div class="breath-stage"><div class="breath-orb" data-orb><div><strong data-phase>Ready</strong><span data-count>4-minute practice</span></div></div></div><p class="meditation-note" data-guide>Find a comfortable position. Let your shoulders soften and begin when you are ready.</p><div class="demo-actions"><button class="demo-action" data-start>Begin meditation</button><button class="demo-action secondary" data-reset>Reset</button></div></section></main><script>
const phases=[['Breathe in','Fill the body gently',4,true],['Hold','Stay soft and steady',2,true],['Breathe out','Release without forcing',6,false],['Rest','Notice the quiet',2,false]];let timer=null,index=0,remaining=0,cycles=0;const phase=document.querySelector('[data-phase]'),count=document.querySelector('[data-count]'),guide=document.querySelector('[data-guide]'),orb=document.querySelector('[data-orb]'),start=document.querySelector('[data-start]');function show(){{const item=phases[index];remaining=item[2];phase.textContent=item[0];guide.textContent=item[1];orb.classList.toggle('active',item[3]);count.textContent=remaining+' seconds'}}function tick(){{remaining-=1;if(remaining<=0){{index=(index+1)%phases.length;if(index===0)cycles+=1;if(cycles>=12){{clearInterval(timer);timer=null;phase.textContent='Complete';count.textContent='Carry the calm with you';guide.textContent='Notice how you feel before returning to your day.';orb.classList.remove('active');start.textContent='Begin again';return}}show()}}else count.textContent=remaining+' seconds'}}start.addEventListener('click',()=>{{if(timer){{clearInterval(timer);timer=null;start.textContent='Continue';guide.textContent='Paused. Keep breathing naturally.';return}}if(phase.textContent==='Ready'||phase.textContent==='Complete'){{index=0;cycles=0;show()}}timer=setInterval(tick,1000);start.textContent='Pause'}});document.querySelector('[data-reset]').addEventListener('click',()=>{{clearInterval(timer);timer=null;index=0;cycles=0;phase.textContent='Ready';count.textContent='4-minute practice';guide.textContent='Find a comfortable position. Let your shoulders soften and begin when you are ready.';orb.classList.remove('active');start.textContent='Begin meditation'}});
</script></body></html>'''

MOTIVATION_PAGE = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="../static/user.css">{DEMO_PAGE_STYLE}<style>
.quote-card{{min-height:360px;display:flex;flex-direction:column;justify-content:center;text-align:center}}.quote-mark{{color:#b9e68c;font:600 64px/1 Georgia,serif}}blockquote{{max-width:700px;margin:8px auto 20px;font:500 clamp(29px,5vw,48px)/1.18 Georgia,serif;letter-spacing:-.03em}}.quote-card cite{{color:#aaa9a2;font-style:normal;font-size:13px}}.quote-dots{{display:flex;gap:7px;justify-content:center;margin-top:28px}}.quote-dots i{{width:7px;height:7px;border-radius:50%;background:#3a4237}}.quote-dots i.active{{background:#b9e68c}}
</style></head><body><main class="demo-wrap"><p class="demo-kicker">Daily perspective</p><h1>A calmer kind of motivation.</h1><p class="demo-lead">Small reminders to support steady effort without pressure or perfectionism.</p><section class="demo-card quote-card"><span class="quote-mark">“</span><blockquote data-quote></blockquote><cite data-source></cite><div class="quote-dots" data-dots></div><div class="demo-actions"><button class="demo-action" data-next>Another thought</button></div></section></main><script>
const quotes=[['You do not need to see the whole path. Take the next clear step.','A Resona reflection'],['Consistency can be quiet. Progress does not need to announce itself.','A Resona reflection'],['Rest is not a reward for finishing everything. It is part of continuing well.','A Resona reflection'],['Make the task smaller, then begin before you feel completely ready.','A Resona reflection'],['A difficult day can still contain one meaningful moment.','A Resona reflection']];let index=0;const quote=document.querySelector('[data-quote]'),source=document.querySelector('[data-source]'),dots=document.querySelector('[data-dots]');quotes.forEach((_,i)=>{{const dot=document.createElement('i');dots.append(dot)}});function show(){{quote.textContent=quotes[index][0];source.textContent=quotes[index][1];[...dots.children].forEach((dot,i)=>dot.classList.toggle('active',i===index))}}document.querySelector('[data-next]').addEventListener('click',()=>{{index=(index+1)%quotes.length;show()}});show();
</script></body></html>'''

SLEEP_TIMER_PAGE = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="../static/user.css">{DEMO_PAGE_STYLE}<style>
.timer-card{{text-align:center}}.timer-display{{margin:30px 0 8px;color:#f3f1e9;font:600 clamp(68px,15vw,124px)/1 Georgia,serif;font-variant-numeric:tabular-nums;letter-spacing:-.06em}}.timer-status{{color:#aaa9a2}}.duration-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin-top:32px}}.duration-grid button{{min-height:54px;border:1px solid rgba(255,255,255,.1);border-radius:16px;background:#171d16;color:#f3f1e9}}.duration-grid button.active{{border-color:#b9e68c;background:#263220;color:#b9e68c}}@media(max-width:520px){{.duration-grid{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><main class="demo-wrap"><p class="demo-kicker">Sleep timer</p><h1>Let the evening soften.</h1><p class="demo-lead">Choose a duration and allow the screen to become a quiet bedside countdown.</p><section class="demo-card timer-card"><div class="timer-display" data-time>20:00</div><p class="timer-status" data-status>Ready when you are</p><div class="duration-grid"><button data-minutes="10">10 min</button><button class="active" data-minutes="20">20 min</button><button data-minutes="30">30 min</button><button data-minutes="45">45 min</button></div><div class="demo-actions"><button class="demo-action" data-start>Start timer</button><button class="demo-action secondary" data-reset>Reset</button></div></section></main><script>
let selected=20,remaining=selected*60,timer=null;const display=document.querySelector('[data-time]'),status=document.querySelector('[data-status]'),start=document.querySelector('[data-start]');function render(){{const minutes=Math.floor(remaining/60),seconds=remaining%60;display.textContent=String(minutes).padStart(2,'0')+':'+String(seconds).padStart(2,'0')}}function stop(){{clearInterval(timer);timer=null}}document.querySelectorAll('[data-minutes]').forEach(button=>button.addEventListener('click',()=>{{stop();selected=Number(button.dataset.minutes);remaining=selected*60;document.querySelectorAll('[data-minutes]').forEach(item=>item.classList.toggle('active',item===button));status.textContent='Ready when you are';start.textContent='Start timer';render()}}));start.addEventListener('click',()=>{{if(timer){{stop();status.textContent='Paused';start.textContent='Continue';return}}if(remaining<=0)remaining=selected*60;status.textContent='Rest well';start.textContent='Pause';timer=setInterval(()=>{{remaining-=1;render();if(remaining<=0){{stop();status.textContent='Timer complete · Good night';start.textContent='Start again'}}}},1000)}});document.querySelector('[data-reset]').addEventListener('click',()=>{{stop();remaining=selected*60;status.textContent='Ready when you are';start.textContent='Start timer';render()}});render();
</script></body></html>'''

DEMO_RESPONSES = {
    "meditation": {"label": "Meditation", "file": "pages/demo-meditation.html", "html": MEDITATION_PAGE, "summary": "Created the guided meditation page."},
    "motivation": {"label": "Motivation", "file": "pages/demo-motivation.html", "html": MOTIVATION_PAGE, "summary": "Created the motivation quotes page."},
    "sleep-timer": {"label": "Sleep Timer", "file": "pages/demo-sleep-timer.html", "html": SLEEP_TIMER_PAGE, "summary": "Created the interactive sleep timer page."},
}


def ensure_demo_workspace():
    if not user_root(DEMO_USERNAME).exists():
        initialize_user_storage(DEMO_USERNAME)
    # The Demo's built-in Home and synth hook are protected product surfaces.
    # Repair stale pre-Demo or legacy copies without removing generated showcase pages.
    safe_path(DEMO_USERNAME, "pages/home.html").write_text(default_home_page(), encoding="utf-8")
    safe_path(DEMO_USERNAME, "static/custom_synth.js").write_text(DEMO_CUSTOM_SYNTH, encoding="utf-8")
    notes = safe_path(DEMO_USERNAME, "memory/notes.md")
    notes.write_text("# Resona Demo\n\n" + DEMO_SYSTEM_PROMPT + "\n", encoding="utf-8")


def reset_demo_workspace():
    if user_root(DEMO_USERNAME).exists():
        delete_user_storage(DEMO_USERNAME)
    initialize_user_storage(DEMO_USERNAME)
    ensure_demo_workspace()


def classify_demo_prompt(prompt):
    normalized = re.sub(r"\s+", " ", prompt.casefold()).strip()
    if re.search(r"sleep.{0,30}timer|timer.{0,30}sleep|睡眠.{0,12}(?:计时|定时)|睡觉.{0,12}计时", normalized):
        return "sleep-timer"
    if re.search(r"meditat|guided breath|breathing guide|冥想|呼吸引导", normalized):
        return "meditation"
    if re.search(r"motivat|inspir|quote|encourag|励志|名言|鼓励", normalized):
        return "motivation"
    return None


def install_demo_response(prompt):
    response_name = classify_demo_prompt(prompt)
    if response_name is None:
        raise ValueError("The Demo supports three requests: a meditation guiding page, motivation quotes, or a sleep timer.")
    response = DEMO_RESPONSES[response_name]
    write_user_file(DEMO_USERNAME, response["file"], response["html"])
    nav_path = safe_path(DEMO_USERNAME, "nav.json")
    navigation = json.loads(nav_path.read_text(encoding="utf-8"))
    item = {"id": "demo-" + response_name, "label": response["label"], "icon": "circle", "target_html": response["file"]}
    navigation["nav_items"] = [entry for entry in navigation["nav_items"] if entry.get("id") != item["id"]]
    navigation["nav_items"].append(item)
    navigation["default_page"] = response["file"]
    write_user_file(DEMO_USERNAME, "nav.json", json.dumps(navigation, indent=2))
    return {"summary": response["summary"], "steps": 1, "tools": ["demo_template"], "page": response["file"]}
