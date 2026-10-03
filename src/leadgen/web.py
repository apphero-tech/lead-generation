"""Local web interface: choose country / state / city (or one institution), run, watch, download."""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import hmac

from flask import Flask, Response, abort, jsonify, request, send_file

from .config import Settings
from .db import connect, set_step
from .export import export_state
from .ipeds import load_institutions
from .pipeline import Pipeline
from .progress import Cancelled, Progress

log = logging.getLogger(__name__)

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana",
    "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "PR": "Puerto Rico", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin",
    "WY": "Wyoming",
}
# Rough durations used for the estimate shown before launching.
MINUTES_NEW_INSTITUTION = 4.0
MINUTES_CACHED_INSTITUTION = 0.5


def select_institutions(conn, state: str, city: str = "", unitid: str = "") -> List[dict]:
    sql, args = "SELECT * FROM institutions WHERE state = ?", [state]
    if unitid:
        sql += " AND unitid = ?"
        args.append(unitid)
    elif city:
        sql += " AND lower(city) = ? AND is_system = 0"
        args.append(city.lower())
    sql += " ORDER BY is_system DESC, name"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:40]


class JobRunner:
    def __init__(self, settings: Settings):
        self.s = settings
        self.jobs: Dict[str, Progress] = {}
        self.lock = threading.Lock()

    def running(self) -> Optional[str]:
        for jid, p in self.jobs.items():
            if p.snapshot()["status"] in ("starting", "running"):
                return jid
        return None

    def start(self, state: str, city: str, unitid: str, label: str) -> str:
        with self.lock:
            if self.running():
                raise RuntimeError("A search is already running")
            jid = uuid.uuid4().hex[:10]
            prog = Progress()
            prog.update(label=label)
            self.jobs[jid] = prog
        threading.Thread(target=self._run, args=(prog, state, city, unitid, label), daemon=True).start()
        return jid

    def _run(self, prog: Progress, state: str, city: str, unitid: str, label: str) -> None:
        conn = connect(self.s.db_path)
        insts: List[dict] = []
        try:
            prog.update(status="running", stage="Chargement de la liste des établissements")
            load_institutions(conn, self.s, state)
            insts = select_institutions(conn, state, city, unitid)
            prog.update(institutions_total=len(insts))
            pipe = Pipeline(conn, self.s, prog)
            for n, inst in enumerate(insts):
                prog.check()
                prog.update(current=inst["name"], stage="Démarrage")
                try:
                    pipe.run_institution(inst)
                except Cancelled:
                    raise
                except Exception as exc:  # one broken site must not stop the whole run
                    log.exception("Failed on %s", inst["name"])
                    set_step(conn, inst["unitid"], "extract", "failed", str(exc)[:300])
                prog.update(institutions_done=n + 1)
            final = "done"
        except Cancelled:
            final = "cancelled"
        except Exception as exc:
            log.exception("Job failed")
            prog.update(error=str(exc)[:300])
            final = "failed"
        try:
            if insts:
                prog.update(stage="Création du fichier Excel")
                name = f"contacts_{state}_{slug(label)}_{datetime.now():%Y%m%d-%H%M}.xlsx"
                path = export_state(conn, state, self.s.out_dir, [i["unitid"] for i in insts], name)
                prog.update(file=str(path))
        finally:
            prog.update(status=final, stage="", current="", finished_at=time.time())
            conn.close()


PAGE = r"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Recherche de contacts</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--text:#1b2430;--muted:#5f6b7a;--line:#dde2e8;--accent:#1f4e9c;--accent-text:#fff;
--ok:#1d7a46;--warn:#9a6200;--err:#b3261e;--bar:#e6ebf2}
@media (prefers-color-scheme:dark){:root{--bg:#12161c;--card:#1b2129;--text:#e6eaef;--muted:#9aa5b3;--line:#2c3440;
--accent:#6d9eea;--accent-text:#0d1420;--ok:#5cc28a;--warn:#e0a84a;--err:#f08b84;--bar:#28303b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:760px;margin:0 auto;padding:32px 16px 60px}
h1{font-size:24px;margin:0 0 4px}p.sub{color:var(--muted);margin:0 0 24px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px;margin-bottom:16px}
label{display:block;font-weight:600;margin:14px 0 6px}label:first-child{margin-top:0}
.hint{font-weight:400;color:var(--muted);font-size:13px}
select,input{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--text);font:inherit}
.row{display:flex;gap:12px}.row>div{flex:1;min-width:0}@media (max-width:560px){.row{flex-direction:column;gap:0}}
button{font:inherit;font-weight:600;padding:11px 18px;border-radius:8px;border:1px solid var(--accent);cursor:pointer}
.primary{background:var(--accent);color:var(--accent-text)}.secondary{background:transparent;color:var(--accent)}
button:disabled{opacity:.5;cursor:not-allowed}
.estimate{margin:16px 0;padding:12px;border-radius:8px;background:var(--bar);font-size:14px}
.counters{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:14px 0}
@media (max-width:560px){.counters{grid-template-columns:repeat(2,1fr)}}
.counter{border:1px solid var(--line);border-radius:10px;padding:12px;text-align:center}
.counter b{display:block;font-size:26px;font-variant-numeric:tabular-nums}.counter span{color:var(--muted);font-size:13px}
.bar{height:10px;background:var(--bar);border-radius:6px;overflow:hidden}.bar>div{height:100%;background:var(--accent);width:0;transition:width .4s}
.status{margin:10px 0 0;color:var(--muted);font-size:14px;min-height:21px}
.done{color:var(--ok);font-weight:600}.err{color:var(--err);font-weight:600}
a.download{display:inline-block;margin-top:14px;text-decoration:none}
ul.files{list-style:none;padding:0;margin:0}ul.files li{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-top:1px solid var(--line);font-size:14px}
ul.files li:first-child{border-top:0}ul.files a{color:var(--accent);word-break:break-all}
.hidden{display:none}
</style></head><body><main>
<h1>Recherche de contacts universitaires</h1>
<p class="sub">17 profils cibles (admissions, advancement, IT/CRM, formation continue). Sources publiques gratuites uniquement.</p>

<div class="card" id="form">
  <label for="country">Pays</label>
  <select id="country"><option value="US">États-Unis</option><option disabled>Autres pays : bientôt</option></select>
  <div class="row">
    <div><label for="state">État</label><select id="state"><option value="">Choisir un état…</option></select></div>
    <div><label for="city">Ville <span class="hint">(facultatif)</span></label><select id="city" disabled><option value="">Toutes les villes</option></select></div>
  </div>
  <label for="inst">Établissement <span class="hint">(facultatif, pour en traiter un seul)</span></label>
  <select id="inst" disabled><option value="">Tous les établissements de la sélection</option></select>
  <div class="estimate hidden" id="estimate"></div>
  <button class="primary" id="start" disabled>Lancer la recherche</button>
</div>

<div class="card hidden" id="job">
  <div id="jobLabel" style="font-weight:600"></div>
  <div class="counters">
    <div class="counter"><b id="cInst">0/0</b><span>établissements</span></div>
    <div class="counter"><b id="cPages">0</b><span>pages lues</span></div>
    <div class="counter"><b id="cContacts">0</b><span>contacts trouvés</span></div>
    <div class="counter"><b id="cEmails">0</b><span>emails publiés</span></div>
  </div>
  <div class="bar"><div id="bar"></div></div>
  <p class="status" id="status"></p>
  <button class="secondary" id="cancel">Arrêter (le fichier sera créé avec ce qui est déjà trouvé)</button>
  <div id="result"></div>
</div>

<div class="card"><div style="font-weight:600;margin-bottom:8px">Fichiers déjà créés</div><ul class="files" id="files"></ul></div>
</main>
<script>
const $=id=>document.getElementById(id);let places=null,job=null,timer=null;
const fmt=s=>{const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;return (h?h+' h ':'')+(h||m?m+' min ':'')+x+' s'};
async function api(u,o){const r=await fetch(u,o);if(!r.ok)throw new Error((await r.json()).error||r.statusText);return r.json()}
async function init(){
  const st=await api('/api/states');st.forEach(s=>$('state').add(new Option(s.name+' ('+s.code+')',s.code)));
  loadFiles();const run=await api('/api/jobs/current');if(run.id){job=run.id;showJob();poll()}
}
$('state').onchange=async()=>{
  const s=$('state').value;['city','inst'].forEach(i=>{$(i).length=1;$(i).disabled=true});$('start').disabled=true;$('estimate').classList.add('hidden');
  if(!s)return;$('estimate').textContent='Chargement de la liste officielle des établissements (IPEDS)…';$('estimate').classList.remove('hidden');
  places=await api('/api/places?state='+s);
  places.cities.forEach(c=>$('city').add(new Option(c.city+' ('+c.n+')',c.city)));$('city').disabled=false;fillInst();$('start').disabled=false;
};
$('city').onchange=fillInst;$('inst').onchange=estimate;
function fillInst(){const c=$('city').value;$('inst').length=1;
  places.institutions.filter(i=>!c||i.city===c).forEach(i=>$('inst').add(new Option(i.name+(i.city?' — '+i.city:''),i.unitid)));
  $('inst').disabled=false;estimate()}
function estimate(){const c=$('city').value,u=$('inst').value;
  let sel=places.institutions.filter(i=>u?i.unitid===u:(!c||i.city===c));if(!u&&!c)sel=sel.concat(places.systems);
  const done=sel.filter(i=>i.done).length,mins=Math.round((sel.length-done)*%NEW%+done*%CACHED%);
  $('estimate').innerHTML='<b>'+sel.length+'</b> établissement(s) dans la sélection'+(done?' (dont '+done+' déjà traités, plus rapides)':'')+
   '. Durée estimée : <b>'+(mins<60?Math.max(1,mins)+' min':Math.round(mins/6)/10+' h')+'</b>.'+(mins>120?' Vous pouvez laisser tourner : la recherche reprend où elle en était si elle est interrompue.':'');
  $('estimate').classList.remove('hidden')}
$('start').onclick=async()=>{
  const st=$('state'),c=$('city').value,u=$('inst').value;
  const label=u?$('inst').selectedOptions[0].text.split(' — ')[0]:(c||st.selectedOptions[0].text.split(' (')[0]);
  try{const r=await api('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({state:st.value,city:c,unitid:u,label})});job=r.id;showJob();poll()}catch(e){alertBox(e.message)}
};
$('cancel').onclick=()=>{$('cancel').disabled=true;fetch('/api/jobs/'+job+'/cancel',{method:'POST'})};
function alertBox(m){$('estimate').innerHTML='<span class="err">'+m+'</span>';$('estimate').classList.remove('hidden')}
function showJob(){$('job').classList.remove('hidden');$('result').innerHTML='';$('cancel').disabled=false;$('cancel').classList.remove('hidden');$('start').disabled=true}
function poll(){clearTimeout(timer);api('/api/jobs/'+job).then(d=>{
  $('jobLabel').textContent='Recherche : '+(d.label||'');
  $('cInst').textContent=d.institutions_done+'/'+d.institutions_total;$('cPages').textContent=d.pages;
  $('cContacts').textContent=d.contacts;$('cEmails').textContent=d.emails_published;
  const pct=d.institutions_total?d.institutions_done/d.institutions_total*100:0;$('bar').style.width=(d.status==='done'?100:pct)+'%';
  if(d.status==='running'||d.status==='starting'){
    $('status').textContent=(d.current?d.current+' — ':'')+(d.stage||'')+' · '+fmt(d.elapsed);timer=setTimeout(poll,1000);
  }else{
    $('cancel').classList.add('hidden');$('start').disabled=false;
    const msg={done:'<span class="done">Terminé</span>',cancelled:'<span class="err">Arrêté</span>',failed:'<span class="err">Erreur : '+(d.error||'')+'</span>'}[d.status];
    $('status').innerHTML=msg+' en '+fmt(d.elapsed)+'.';
    if(d.file)$('result').innerHTML='<a class="download" href="/api/jobs/'+job+'/download"><button class="primary">Télécharger le fichier Excel</button></a>';
    loadFiles()}
}).catch(()=>{timer=setTimeout(poll,3000)})}
async function loadFiles(){const f=await api('/api/files');$('files').innerHTML=f.length?f.map(x=>
  '<li><a href="/files/'+encodeURIComponent(x.path)+'">'+x.name+'</a><span>'+x.date+'</span></li>').join(''):'<li>Aucun fichier pour l’instant.</li>'}
init();
</script></body></html>"""


def create_app(settings: Settings) -> Flask:
    app = Flask(__name__)
    runner = JobRunner(settings)

    @app.before_request
    def require_password():
        if not settings.ui_password:
            return None
        auth = request.authorization
        if auth and hmac.compare_digest(auth.username or "", settings.ui_user) and \
                hmac.compare_digest(auth.password or "", settings.ui_password):
            return None
        return Response("Mot de passe requis", 401, {"WWW-Authenticate": 'Basic realm="Contacts"'})

    @app.get("/")
    def index():
        return PAGE.replace("%NEW%", str(MINUTES_NEW_INSTITUTION)).replace("%CACHED%", str(MINUTES_CACHED_INSTITUTION))

    @app.get("/api/states")
    def states():
        return jsonify([{"code": k, "name": v} for k, v in sorted(US_STATES.items(), key=lambda x: x[1])])

    @app.get("/api/places")
    def places():
        state = request.args.get("state", "").upper()
        if state not in US_STATES:
            abort(400)
        conn = connect(settings.db_path)
        try:
            load_institutions(conn, settings, state)
            done = {r["unitid"] for r in conn.execute(
                "SELECT unitid FROM step_status WHERE step = 'crawl' AND status = 'done'")}
            insts = select_institutions(conn, state)
        finally:
            conn.close()
        campuses = [i for i in insts if not i["is_system"]]
        cities: Dict[str, int] = {}
        for i in campuses:
            if i["city"]:
                cities[i["city"]] = cities.get(i["city"], 0) + 1
        def item(i):
            return {"unitid": i["unitid"], "name": i["name"], "city": i["city"] or "", "done": i["unitid"] in done}
        return jsonify({
            "cities": [{"city": c, "n": n} for c, n in sorted(cities.items())],
            "institutions": [item(i) for i in campuses],
            "systems": [item(i) for i in insts if i["is_system"]],
        })

    @app.post("/api/jobs")
    def start_job():
        body = request.get_json(force=True)
        state = (body.get("state") or "").upper()
        if state not in US_STATES:
            return jsonify(error="Choisissez un état"), 400
        try:
            jid = runner.start(state, body.get("city", ""), body.get("unitid", ""), body.get("label") or state)
        except RuntimeError:
            return jsonify(error="Une recherche est déjà en cours"), 409
        return jsonify(id=jid)

    @app.get("/api/jobs/current")
    def current_job():
        return jsonify(id=runner.running())

    @app.get("/api/jobs/<jid>")
    def job_status(jid):
        prog = runner.jobs.get(jid) or abort(404)
        return jsonify(prog.snapshot())

    @app.post("/api/jobs/<jid>/cancel")
    def cancel_job(jid):
        prog = runner.jobs.get(jid) or abort(404)
        prog.cancel_requested = True
        return jsonify(ok=True)

    @app.get("/api/jobs/<jid>/download")
    def download(jid):
        prog = runner.jobs.get(jid) or abort(404)
        path = prog.snapshot().get("file") or abort(404)
        return send_file(path, as_attachment=True)

    @app.get("/api/files")
    def files():
        out = []
        for p in sorted(settings.out_dir.glob("*/*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]:
            out.append({"name": p.name, "path": str(p.relative_to(settings.out_dir)),
                        "date": datetime.fromtimestamp(p.stat().st_mtime).strftime("%d/%m/%Y %H:%M")})
        return jsonify(out)

    @app.get("/files/<path:rel>")
    def get_file(rel):
        path = (settings.out_dir / rel).resolve()
        if settings.out_dir.resolve() not in path.parents or not path.exists():
            abort(404)
        return send_file(path, as_attachment=True)

    return app
