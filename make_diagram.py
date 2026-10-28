#!/usr/bin/env python3
"""Generate the README architecture diagram, one SVG per colour scheme.

Two files rather than one self-switching file: GitHub strips <style> blocks
from inline SVG, so a `prefers-color-scheme` media query inside the document is
silently dropped. The README pairs them with <picture>, which GitHub honours.

A README diagram is studied, not narrated, so it is COMPLETE rather than
minimal: the browser and the public S3 site, Cognito, the JWT-protected
HTTP API and its API function, the private data bucket holding the corpus
and answers, the SQS queue and its dead-letter queue, the RAG worker, the
local ingest script, Bedrock, and the DynamoDB table are all on it.
Run:  python3 make_diagram.py
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------------------------
# Geometry and style (shared with the other projects' diagrams)
# ------------------------------------------------------------------------------
NW, NH = 420, 110                          # node width, height
GUTTER = 140                               # between columns: room for a label
MARGIN = 40
COL = [MARGIN + i * (NW + GUTTER) for i in range(3)]
ROW = [40 + i * 200 for i in range(4)]
W = MARGIN * 2 + NW * 3 + GUTTER * 2
H = ROW[-1] + NH + 40 + 0
TITLE_PX, SUB_PX, EDGE_PX = 26, 17, 16

THEMES = {
    "dark": {
        "bg": "#0d1117", "panel": "#161b22", "text": "#e6edf3",
        "muted": "#8b949e", "line": "#58a6ff", "local": "#39c5bb",
        "purple": "#c39be8", "navy": "#c8d6e8", "blue": "#58a6ff",
        "amber": "#f2c163", "store": "#7ee787",
    },
    "light": {
        "bg": "#ffffff", "panel": "#f6f8fa", "text": "#1f2328",
        "muted": "#59636e", "line": "#0969da", "local": "#137e77",
        "purple": "#8250df", "navy": "#475467", "blue": "#0969da",
        "amber": "#9a6700", "store": "#1a7f37",
    },
}

# Lucide icon paths, on a 24-unit grid.
ICONS = {
    "globe": ['<circle cx="12" cy="12" r="10"/>',
              '<path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/>',
              '<path d="M2 12h20"/>'],
    "route": ['<circle cx="6" cy="19" r="3"/>', '<circle cx="18" cy="5" r="3"/>',
              '<path d="M12 19h4.5a3.5 3.5 0 0 0 0-7h-8a3.5 3.5 0 0 1 0-7H12"/>'],
    "network": ['<rect x="16" y="16" width="6" height="6" rx="1"/>',
                '<rect x="2" y="16" width="6" height="6" rx="1"/>',
                '<rect x="9" y="2" width="6" height="6" rx="1"/>',
                '<path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3"/>',
                '<path d="M12 12V8"/>'],
    "layers": ['<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83'
               'l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/>',
               '<path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/>',
               '<path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>'],
    "database": ['<ellipse cx="12" cy="5" rx="9" ry="3"/>',
                 '<path d="M3 5V19A9 3 0 0 0 21 19V5"/>',
                 '<path d="M3 12A9 3 0 0 0 21 12"/>'],
    "key": ['<path d="M2.586 17.414A2 2 0 0 0 2 18.828V21a1 1 0 0 0 1 1h3a1 1 0 0 0 '
            '1-1v-1a1 1 0 0 1 1-1h1a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h.172a2 2 0 0 0 '
            '1.414-.586l.814-.814a6.5 6.5 0 1 0-4-4z"/>',
            '<circle cx="16.5" cy="7.5" r=".5"/>'],
    "monitor": ['<rect width="20" height="14" x="2" y="3" rx="2"/>',
                '<line x1="8" x2="16" y1="21" y2="21"/>',
                '<line x1="12" x2="12" y1="17" y2="21"/>'],
    "terminal": ['<polyline points="4 17 10 11 4 5"/>',
                 '<line x1="12" x2="20" y1="19" y2="19"/>'],
    "users": ['<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>',
              '<circle cx="9" cy="7" r="4"/>',
              '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/>',
              '<path d="M16 3.13a4 4 0 0 1 0 7.75"/>'],
    "file": ['<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>',
             '<path d="M14 2v4a2 2 0 0 0 2 2h4"/>', '<path d="M10 9H8"/>',
             '<path d="M16 13H8"/>', '<path d="M16 17H8"/>'],
}

NODES = {}


def _r(nid):
    """Return (left, top, right, bottom, centre-x, centre-y) for a node."""
    x, y = NODES[nid][0], NODES[nid][1]
    return x, y, x + NW, y + NH, x + NW / 2, y + NH / 2


def gutter_x(c):
    """Centre of the gutter to the right of column c."""
    return COL[c] + NW + GUTTER / 2


def gutter_y(r):
    """Centre of the gap below row r."""
    return ROW[r] + NH + (ROW[1] - ROW[0] - NH) / 2


# ------------------------------------------------------------------------------
# Layout
# ------------------------------------------------------------------------------
# The browser is at the top between its site (left) and Cognito (right), with
# the API under it and the API function to the right. The data bucket is on the
# left, reached by the API function over the top and by the worker from below.
# Jobs go through the queue to the worker; the worker fetches postings, calls
# Bedrock and records status in the table. The dead-letter queue is bottom
# right.
#
# id: (x, y, colour key, icon, title, subtitle[, "dashed"])
NODES.update({
    "browser":  (COL[1], ROW[0], "navy", "globe", "Browser",
                 "chat SPA"),
    "web":      (COL[0], ROW[0], "store", "layers", "rag-app-<hex>",
                 "public S3 bucket: SPA"),
    "cognito":  (COL[2], ROW[0], "purple", "users", "Cognito user pool",
                 "Hosted UI, email sign-in"),
    "data":     (COL[0], ROW[1], "store", "layers", "rag-data-<hex>",
                 "corpus/ + per-query Q&A files"),
    "api":      (COL[1], ROW[1], "amber", "route", "rag-api-<hex>",
                 "HTTP API, JWT authorizer"),
    "fn":       (COL[2], ROW[1], "blue", "terminal", "rag-api function",
                 "python3.11, every route"),
    "worker":   (COL[0], ROW[2], "blue", "terminal", "rag-worker",
                 "python3.11 + numpy, 512 MB, 300 s"),
    "sqs":      (COL[1], ROW[2], "amber", "layers", "rag-query-requests",
                 "SQS, 6 min visibility, batch 1"),
    "ddb":      (COL[2], ROW[2], "store", "database", "DynamoDB",
                 "pk + sk: usage, conversations, queries"),
    "sites":    (COL[0], ROW[3], "navy", "file", "03-ingest/ingest.py",
                 "interview .txt, GitHub, YouTube"),
    "bedrock":  (COL[1], ROW[3], "purple", "layers", "Amazon Bedrock",
                 "Haiku 4.5 + Titan Embeddings v2"),
    "dlq":      (COL[2], ROW[3], "amber", "layers", "rag-query-requests-dlq",
                 "after 1 receive, 14 days", "dashed"),
})

br, web, cg, dt = _r("browser"), _r("web"), _r("cognito"), _r("data")
api, fn, wk, sq, db = _r("api"), _r("fn"), _r("worker"), _r("sqs"), _r("ddb")
st, bd, dl = _r("sites"), _r("bedrock"), _r("dlq")

# id: (path, colour key, label, label_x, label_y, anchor[, "dashed"])
EDGES = {
    "e_page":   (f"M{br[0]},{br[5]} H{web[2] + 5}", "store",
                 "GET page", gutter_x(0), br[5] - 12, "middle"),
    "e_auth":   (f"M{br[2]},{br[5]} H{cg[0] - 5}", "purple",
                 "sign in, JWT", gutter_x(1), br[5] - 12, "middle"),
    "e_api":    (f"M{br[4]},{br[3]} V{api[1] - 5}", "line",
                 "Bearer JWT", br[4] + 14, gutter_y(0) + 20, "start"),
    "e_fn":     (f"M{api[2]},{api[5]} H{fn[0] - 5}", "line",
                 "all routes", gutter_x(1), api[5] - 12, "middle"),
    "e_s3api":  (f"M{fn[4]},{fn[1]} V{gutter_y(0)} H{dt[4]} V{dt[1] - 5}",
                 "store", "questions", dt[4] + 14,
                 gutter_y(0) - 10, "start"),
    "e_row":    (f"M{fn[4]},{fn[3]} V{db[1] - 5}", "store",
                 "items", fn[4] + 14, gutter_y(1) + 6, "start"),
    "e_enq":    (f"M{fn[4] - 120},{fn[3]} V{gutter_y(1)} H{sq[4]} V{sq[1] - 5}",
                 "amber", "POST .../queries", sq[4] + 14, gutter_y(1) - 10, "start"),
    "e_trig":   (f"M{sq[0]},{sq[5]} H{wk[2] + 5}", "amber",
                 "trigger", gutter_x(0), sq[5] - 12, "middle"),
    "e_s3wk":   (f"M{wk[4]},{wk[1]} V{dt[3] + 5}", "store",
                 "corpus, answers", wk[4] + 14, gutter_y(1) + 6, "start"),
    "e_embed":  (f"M{st[2]},{st[5]} H{bd[0] - 5}", "purple",
                 "embed", gutter_x(0), st[5] - 12, "middle"),
    "e_bed":    (f"M{wk[4] + 80},{wk[3]} V{gutter_y(2) + 15} H{bd[4]} V{bd[1] - 5}",
                 "purple", "InvokeModel", bd[4] + 14, gutter_y(2) + 35,
                 "start"),
    "e_stat":   (f"M{wk[4] + 160},{wk[3]} V{gutter_y(2) - 15} H{db[4]} V{db[3] + 5}",
                 "store", "status, tokens", db[4] + 14, gutter_y(2) + 6,
                 "start"),
    "e_dlq":    (f"M{sq[4] + 180},{sq[3]} V{gutter_y(2) + 30} H{dl[4] - 120} V{dl[1] - 5}",
                 "amber", "redrive", dl[4] - 106, gutter_y(2) + 22, "start",
                 "dashed"),
}

ALT = ("A browser loads the chat app from the public rag-app S3 bucket and "
       "signs in through the Cognito Hosted UI. It calls rag-api, an HTTP API "
       "with a Cognito JWT authorizer, whose single function stores the question "
       "in the rag-data bucket, records the query in DynamoDB and puts it on the "
       "rag-query-requests SQS queue. The queue triggers rag-worker, which loads "
       "the corpus embeddings from S3, embeds the question with Titan Embeddings "
       "v2 on Bedrock, picks the 10 closest chunks, adds the last five Q&A "
       "pairs, asks Claude Haiku for the answer, writes the answer and sources "
       "to S3, and updates the query and the user's token usage. A message whose "
       "run fails goes to the dead-letter queue. The corpus is built beforehand "
       "by 03-ingest/ingest.py from local interview and background text files, "
       "GitHub READMEs and YouTube descriptions, embedded with Titan.")

# ------------------------------------------------------------------------------
# Rendering
# ------------------------------------------------------------------------------
from html import escape

FONT = "-apple-system, BlinkMacSystemFont, Segoe UI, Helvetica, Arial, sans-serif"


def node_svg(nid, t):
    """Render one service box: rounded panel, icon, title, subtitle."""
    x, y, key, icon, title, sub, *opt = NODES[nid]
    title, sub = escape(title), escape(sub)
    colour = t[key]
    dash = ' stroke-dasharray="10 7"' if opt and opt[0] == "dashed" else ""
    scale = 1.25
    isz = 24 * scale
    ix, iy = x + 20, y + (NH - isz) / 2
    tx = x + 20 + isz + 14
    return "".join([
        f'<rect x="{x}" y="{y}" width="{NW}" height="{NH}" rx="10" ry="10" '
        f'fill="{t["panel"]}" stroke="{colour}" stroke-width="2"{dash}/>',
        f'<g transform="translate({ix},{iy}) scale({scale})" fill="none" '
        f'stroke="{colour}" stroke-width="2" stroke-linecap="round" '
        f'stroke-linejoin="round">' + "".join(ICONS[icon]) + "</g>",
        f'<text x="{tx}" y="{y + NH/2 - 4}" font-family="{FONT}" '
        f'font-size="{TITLE_PX}" font-weight="600" fill="{t["text"]}">{title}</text>',
        f'<text x="{tx}" y="{y + NH/2 + 22}" font-family="{FONT}" '
        f'font-size="{SUB_PX}" fill="{t["muted"]}">{sub}</text>',
    ])


def edge_svg(eid, t):
    """Render one arrow plus its label (on a background chip, so lines that
    pass behind a label never cut through its text)."""
    d, key, label, lx, ly, anchor, *opt = EDGES[eid]
    label = escape(label)
    colour = t[key]
    dash = ' stroke-dasharray="8 6"' if opt and opt[0] == "dashed" else ""
    out = (f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="2"{dash} '
           f'marker-end="url(#a_{eid})"/>')
    if label:
        out += (f'<text x="{lx}" y="{ly}" text-anchor="{anchor}" '
                f'font-family="{FONT}" font-size="{EDGE_PX}" font-weight="600" '
                f'fill="{colour}" stroke="{t["bg"]}" stroke-width="6" '
                f'paint-order="stroke">{label}</text>')
    return out


def build(t):
    """Assemble the diagram for one theme."""
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'viewBox="0 0 {W} {H}" role="img" aria-label="{ALT}">', "<defs>"]
    # One marker per edge: a shared marker cannot carry per-edge colour.
    for eid, spec in EDGES.items():
        out.append(
            f'<marker id="a_{eid}" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="5" markerHeight="5" orient="auto-start-reverse">'
            f'<path d="M0,0 L10,5 L0,10 z" fill="{t[spec[1]]}"/></marker>')
    out.append("</defs>")
    out.append(f'<rect width="{W}" height="{H}" fill="{t["bg"]}"/>')
    for eid in EDGES:
        out.append(edge_svg(eid, t))
    for nid in NODES:
        out.append(node_svg(nid, t))
    out.append("</svg>")
    return "".join(out)


def main():
    for name, theme in THEMES.items():
        path = os.path.join(HERE, f"architecture-{name}.svg")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(build(theme))
        print(f"  architecture-{name}.svg")


if __name__ == "__main__":
    main()
