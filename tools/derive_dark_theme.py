#!/usr/bin/env python3
"""Derive the dark palette from the light one and the declared contrast pairs.

    python3 tools/derive_dark_theme.py /tmp/dark.json

A ONE-OFF, kept because it documents how design/tokens.json's `dark` block was
produced and lets it be re-derived if the light palette moves. It is NOT part of the
build: tools/gen_tokens.py reads the committed dark values, it does not compute them.

Surfaces and the ink ramp are chosen; every chromatic token is DERIVED — same hue and
saturation, lightness raised until it clears its declared pairs on the dark surfaces.
Nothing here is hand-picked per token, so the palette cannot quietly drift out of AA."""
import colorsys, json, sys
def rgb(h):
    h=h.lstrip('#')
    if len(h)==3: h=''.join(c*2 for c in h)
    return [int(h[i:i+2],16) for i in (0,2,4)]
def hexs(*c): return "#%02x%02x%02x"%tuple(round(x) for x in c)
def lum(h):
    r,g,b=[c/255 for c in rgb(h)]
    f=lambda c: c/12.92 if c<=0.03928 else ((c+0.055)/1.055)**2.4
    return .2126*f(r)+.7152*f(g)+.0722*f(b)
def cr(a,b):
    la,lb=lum(a),lum(b); hi,lo=max(la,lb),min(la,lb); return (hi+.05)/(lo+.05)
def tint(fg,base,a):
    f,b=rgb(fg),rgb(base); return hexs(*[f[i]*a+b[i]*(1-a) for i in range(3)])
def flat(c,base):
    if c.startswith("rgba"):
        n=[float(x) for x in c[c.index("(")+1:c.index(")")].split(",")]
        return tint(hexs(*n[:3]), base, n[3])
    if len(c)==9:
        h=c.lstrip('#'); return tint('#'+h[:6], base, int(h[6:8],16)/255)
    return c
def lighten_to(v,bgs,t):
    r,g,b=[c/255 for c in rgb(v)]; hh,ll,ss=colorsys.rgb_to_hls(r,g,b)
    for s in range(1001):
        rr,gg,bb=colorsys.hls_to_rgb(hh,ll+(1-ll)*s/1000,ss)
        c=hexs(rr*255,gg*255,bb*255)
        if all(cr(c,x)>=t for x in bgs): return c
    return None

L=json.load(open("design/tokens.json"))["themes"]["light"]
spec={k:v for g in ("color","status") for k,v in L[g].items() if not k.startswith("_")}

D={"bg":"#0f1117","panel":"#171a21","panel2":"#21252e","line":"#262b35","line2":"#333a46",
   "ink":"#e7eaf0","on-solid":"#0f1117","tooltip-fg":"#e7eaf0","tooltip-bg":"#2d333d",
   "code-bg":"#0b0d12","code-fg":"#c9d1d9","option-fg":"#e7eaf0",
   "row-hover":"#1c2028","row-alt":"#1b1f27","row-alt-hover":"#222834","chat-tool-bg":"#1b1f27",
   "exp-bg":"#f59e0b26","exp-line":"#f59e0b66","chg-design-bg":"rgba(157,118,230,.20)",
   # elevation: a dark theme cannot lean on a 4% black shadow
   "sh":"0 1px 2px rgba(0,0,0,.40),0 1px 3px rgba(0,0,0,.30)",
   "sh-lift":"0 10px 30px rgba(0,0,0,.55)"}
P=D["panel"]; SURF=[D["bg"],P,D["panel2"]]

# tinted surfaces, from a provisional lightened hue of their foreground
TINT={"good-bg":("good",.16),"bad-bg":("bad",.16),"warn-bg":("warn",.16),"bad-soft":("bad",.12),
      "acc-soft":("acc",.20),"acc-bg":("acc",.16),"violet-bg":("violet",.18),
      "pill-good-bg":("pill-good-fg",.18),"pill-attn-bg":("pill-attn-fg",.18),
      "pill-bad-bg":("pill-bad-fg",.18),"exact-bg":("exact-fg",.18),"heur-bg":("heur-fg",.18),
      "run-bg":("run-line",.14)}
for bg,(fg,a) in TINT.items():
    D[bg]=tint(lighten_to(spec[fg]["value"],SURF,4.5) or spec[fg]["value"], P, a)
for n in ("good-line","warn-line","bad-line","run-line","tag-ext-line","tag-legacy-line","period-line"):
    D[n]=tint(spec[n]["value"],P,.34)

CHOSEN = set(D)   # everything above was picked by hand; the loops below must not touch it

# every foreground with a declared requirement, against ALL its backgrounds.
# Skips CHOSEN: this loop drives a token to the LOWEST lightness that clears its target,
# which is right for a token whose only requirement is "stay legible" and wrong for one
# that was picked for a reason. Until 2026-08-04 it did not skip, so it silently
# overwrote --ink's chosen #e7eaf0 with a derived #6e8bc5 — 5.11 on --panel, where
# --ink2 was 5.15 and --mut 5.14. Every text token landed on the same 4.5:1 floor and
# the ramp flattened; primary text came out nominally the weakest of the three, and blue,
# since the hue preserved was that of a near-black. Tokens with no declared pair
# (option-fg, code-fg) were unaffected, which is why the bug was not obvious.
for name,s in spec.items():
    if name in CHOSEN: continue
    for key,target in (("text_on",4.5),("graphic_on",3.0)):
        if key in s:
            bgs=list(SURF)+[flat(D.get(b,spec[b]["value"]),P) for b in s[key]]
            D[name]=lighten_to(s["value"],bgs,target)
# chromatic tokens with no declared pair but visible on dark
for n in ("acc-ink","company-empty","swatch-empty"):
    D[n]=lighten_to(spec[n]["value"],SURF,4.5)
D["dup"]=D["acc"]

fails=[]
for name,s in spec.items():
    for key,target in (("text_on",4.5),("graphic_on",3.0)):
        for b in s.get(key,[]):
            r=cr(D[name],flat(D[b],P))
            if r<target: fails.append((name,b,round(r,2),target))
print(f"{len(D)} dark values; failures: {fails or 'none'}")
print("on-solid over fills:", " ".join(f"{k}={cr(D['on-solid'],D[k]):.2f}" for k in ("acc","good","bad","warn")))
json.dump(D, open(sys.argv[1],"w"), indent=1)
