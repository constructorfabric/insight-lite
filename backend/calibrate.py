#!/usr/bin/env python3
"""Calibrate page (/calibrate) — collect GROUND-TRUTH labels for the developer score.

Managers/peers rate people 1–5; those labels are the target the score backtest
validates against (the score itself has no external "good developer" signal). One
rating per rater per subject; re-rating updates. Deliberately plain and private
(org-gated) — this is a calibration tool, not a public scoreboard.
"""
from __future__ import annotations

import html as _h
import json as _json


def calibrate_json(rater: str = "") -> dict:
    """Data for the React /calibrate route: the score board with each person's
    all-rater summary + this rater's own rating, plus the pillar weights. Raw
    values; the React page escapes on output."""
    import store
    conn = store.connect()
    try:
        board = store.developer_scores(conn, "2008-01-01T00:00:00Z",
                                       "2099-01-01T00:00:00Z")["board"]
        summ = store.label_summary(conn)
        mine = {r["subject_login"]: r["rating"]
                for r in store.read_score_labels(conn) if r["rater"] == rater}
    finally:
        conn.close()
    rows = []
    for p in board:
        lg = p["login"]
        s = summ.get(lg)
        avg = (f'{s["mean"]} · {s["n"]} rater' + ("s" if s["n"] != 1 else "")) if s else "—"
        rows.append({"login": lg, "name": p["name"], "score": p["score"],
                     "avg": avg, "mine": mine.get(lg, 0)})
    return {"rater": rater, "board": rows,
            "weights": {"cur": store._score_weights(), "def": dict(store._SCORE_WEIGHTS)}}


_HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Calibrate — Constructor Insight</title>
<style>
  :root{--bg:#f6f8fa;--panel:#fff;--panel2:#eaeef2;--line:#d0d7de;--ink:#1f2328;--mut:#656d76;
    --acc:#5b5bf0;--good:#1a7f37;--bad:#cf222e;--star:#f5a623}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
  /*SHELL_CSS*/
  main.wrap{padding:20px 34px 90px}
  h1{font-size:24px;font-weight:800;letter-spacing:-.02em;margin:0 0 4px}
  .sub{color:var(--mut);font-size:13.5px;margin:2px 0 16px;max-width:78ch}
  .exp{font-size:10.5px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:#c77700;
    background:#f59e0b22;border:1px solid #f59e0b55;border-radius:999px;padding:2px 8px;margin-left:8px;vertical-align:middle}
  table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);
    border-radius:12px;overflow:hidden;font-size:14px}
  th,td{padding:10px 12px;text-align:left;border-top:1px solid var(--line)}
  thead th{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);font-weight:700;border-top:none}
  .num{text-align:right;font-variant-numeric:tabular-nums}
  td.who b{font-weight:650} td.who .lg{color:var(--mut);font-size:12px;margin-left:6px}
  td.sc{font-weight:800}
  .stars{white-space:nowrap}
  .star{border:none;background:none;cursor:pointer;font-size:20px;line-height:1;color:var(--line);padding:0 1px}
  .star:hover,.star.on{color:var(--star)}
  .note{width:100%;max-width:220px;border:1px solid var(--line);border-radius:7px;padding:6px 9px;font:inherit;font-size:13px;background:var(--panel)}
  .saved{color:var(--good);font-size:12px;font-weight:700;white-space:nowrap}
  .empty{padding:26px;text-align:center;color:var(--mut)}
  .wtcard{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:26px 0 0;max-width:640px}
  .wtcard h2{font-size:16px;font-weight:800;margin:0 0 3px}
  .wtcard .wsub{color:var(--mut);font-size:12.5px;margin:0 0 14px;line-height:1.5}
  .wtrow{display:grid;grid-template-columns:120px 1fr 68px;align-items:center;gap:12px;padding:5px 0}
  .wtrow .wtlab{font-size:13.5px;font-weight:600}
  .wtrow input[type=range]{width:100%;accent-color:var(--acc)}
  .wtrow .wtval{font-size:14px;font-weight:800;text-align:right;font-variant-numeric:tabular-nums}
  .wtrow .wtval .eff{display:block;font-size:10.5px;font-weight:600;color:var(--mut)}
  .wtfoot{display:flex;align-items:center;gap:14px;margin-top:14px;flex-wrap:wrap}
  .wt-total{font-size:12.5px;color:var(--mut)} .wt-total b{color:var(--ink);font-size:14px}
  .wtbtns{margin-left:auto;display:flex;align-items:center;gap:10px}
  .btn{border:1px solid var(--acc);background:var(--acc);color:#fff;border-radius:8px;padding:7px 14px;
    font:inherit;font-size:13px;font-weight:700;cursor:pointer}
  .btn:disabled{opacity:.55;cursor:default}
  .btn.ghost{background:var(--panel);color:var(--acc)}
  .wtsaved{font-size:12px;font-weight:700;white-space:nowrap}
</style></head>
<body><div class="app"><!--SIDEBAR--><main class="wrap">
<h1>Calibrate the developer score <span class="exp">Ground truth</span></h1>
<p class="sub">/*SUB*/</p>
<div style="overflow-x:auto"><table>
  <thead><tr><th>Person</th><th class="num">Score</th><th>Your rating</th><th class="num">All raters</th><th>Note</th><th></th></tr></thead>
  <tbody><!--ROWS--></tbody>
</table></div>

<section class="wtcard">
  <h2>Pillar weights <span class="exp">Experimental</span></h2>
  <p class="wsub">How the four pillars combine into the compound score. Only the <em>relative</em>
    sizes matter — per person the weights are normalised, and pillars with no data are reweighted
    out (so a person's effective share can differ). Defaults: engagement 20 · delivery 25 ·
    craft 25 · flow 35. Changes apply on the next report load.</p>
  <div id="wtRows"></div>
  <div class="wtfoot">
    <span class="wt-total">Total <b id="wtTotal">100</b> · normalised at scoring</span>
    <span class="wtbtns"><button type="button" id="wtReset" class="btn ghost">Reset to defaults</button><button type="button" id="wtSave" class="btn">Save weights</button><span id="wtStatus" class="wtsaved"></span></span>
  </div>
</section>
</main></div>
<script>
(function(){
  "use strict";
  function save(tr, rating){
    var subject=tr.getAttribute("data-subject");
    var note=(tr.querySelector(".note")||{}).value||"";
    var cell=tr.querySelector(".saved"); cell.textContent="…"; cell.style.color="var(--mut)";
    fetch("/api/score-label",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({subject:subject,rating:rating,note:note})})
      .then(function(r){return r.json();}).then(function(j){
        if(j&&j.ok){ cell.textContent="saved ✓"; cell.style.color="var(--good)"; }
        else { cell.textContent=(j&&j.error)||"failed"; cell.style.color="var(--bad)"; }
      }).catch(function(){ cell.textContent="failed"; cell.style.color="var(--bad)"; });
  }
  document.addEventListener("click", function(e){
    var st=e.target.closest(".star"); if(!st) return;
    var tr=st.closest("tr"), n=+st.getAttribute("data-star");
    var stars=tr.querySelectorAll(".star");
    stars.forEach(function(s,i){ s.classList.toggle("on", i < n); });
    save(tr, n);
  });
})();
(function(){
  "use strict";
  var W=/*WDATA*/;
  var KEYS=[["engagement","Engagement"],["delivery","Delivery"],["craft","Craft & rework"],["flow","Flow"]];
  var cur=Object.assign({}, W.def, W.cur);
  var $=function(s){return document.querySelector(s);};
  function total(){var t=0;KEYS.forEach(function(k){t+=Math.round(cur[k[0]]||0);});return t;}
  function render(){
    var t=total()||1;
    $("#wtRows").innerHTML=KEYS.map(function(k){var v=Math.round(cur[k[0]]||0);
      return '<div class="wtrow"><span class="wtlab">'+k[1]+'</span>'
        +'<input type="range" min="0" max="60" step="1" value="'+v+'" data-wt="'+k[0]+'" aria-label="'+k[1]+' weight">'
        +'<span class="wtval">'+v+'<span class="eff">'+Math.round(100*v/t)+'% of score</span></span></div>';
    }).join("");
    $("#wtTotal").textContent=total();
  }
  function setStatus(txt,color){var s=$("#wtStatus"); s.textContent=txt; s.style.color=color||"var(--mut)";}
  document.addEventListener("input",function(e){
    var w=e.target.getAttribute&&e.target.getAttribute("data-wt"); if(!w) return;
    cur[w]=+e.target.value; render(); setStatus("");
  });
  $("#wtReset").addEventListener("click",function(){ cur=Object.assign({}, W.def); render(); setStatus("reset — not saved yet","var(--mut)"); });
  $("#wtSave").addEventListener("click",function(){
    if(total()<=0){ setStatus("at least one pillar must be > 0","var(--bad)"); return; }
    var btn=this; btn.disabled=true; setStatus("saving…","var(--mut)");
    fetch("/api/score-weights",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({weights:cur})})
      .then(function(r){return r.json();}).then(function(j){
        btn.disabled=false;
        if(j&&j.ok){ W.cur=j.weights; cur=Object.assign({}, j.weights); render();
          setStatus("saved ✓ — new scores on next report load","var(--good)"); }
        else setStatus((j&&j.error)||"save failed","var(--bad)");
      }).catch(function(){ btn.disabled=false; setStatus("save failed","var(--bad)"); });
  });
  render();
})();
</script>
</body></html>
"""
