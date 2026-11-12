"""Publication vector artwork: sessions, learned demand, and native KV reclaim.

The diagram is an original drawing. Lucide symbols are pinned in icons/lucide;
see icons/lucide/LICENSE and design_references/README.md for attribution/references.
Training counts/configuration come from the archived initial model; requests,
bar heights, tree shapes, and candidate scores are schematic.
"""
from pathlib import Path
from io import BytesIO
from html import escape
import re
import copy
import xml.etree.ElementTree as ET

import pymupdf
from matplotlib.font_manager import FontProperties
from matplotlib.mathtext import math_to_image

ASSET = Path(__file__).resolve().parent
CAPTION = (
    r'LeasePath workflow. \textbf{(A)} Shared prefixes, overlapping tools, '
    r'and FCFS inference. '
    r'\textbf{(B)} Observed snapshots train interval classifiers; predicted demand '
    r'masses become discounted retention scores. \textbf{(C)} Legal leaves form '
    r'a logical plan before native reclamation. Counts describe '
    r'the initial fit; histories, example rows, trees, bars, and equal-size-block '
    r'scores are schematic.'
)
INTRO = r'''Figure~\ref{fig:method-overview} connects shared request prefixes to
prediction and physical reclamation. Its training path expands observed
snapshots into risk intervals; boosted classifiers estimate conditional
hazards, which survival recursion converts to demand masses. Current
state selects a distribution even when model weights stay frozen.
Requests retain FCFS order. Under capacity pressure, legal-leaf removals
build a complete logical plan before native KV cells are released;
active state remains protected. Dynamic variants refit on observed
history and swap weights at a request boundary. Unbound leaves are
reclaimed first; insufficient support switches the remaining admission
to retired-first LRU, as detailed below.
'''

TRAINING_DETAILS = (
    'The encoded input has 14 columns, including interval index and\n'
    'missingness flags. The 1,079 snapshots expand into 9,229 observed\n'
    'risk-interval rows; three class trees per boosting round give\n'
    '120 trees in the saved initial model.\n'
)


def revise_training_description(source):
    """Apply the audited feature/training detail to v2-derived or existing text."""
    marker='There are 40 boosting rounds, learning rate 0.05, four leaves, depth two,'
    assert source.count(marker)==1
    if TRAINING_DETAILS not in source:
        source=source.replace(marker,TRAINING_DETAILS+marker,1)
    return source


def keep_block_score_together(source):
    """Keep tall method displays clear of the column boundary."""
    marker='The equal-cost block score is'
    protected='\\Needspace{5\\baselineskip}\n'+marker
    assert source.count(marker)==1
    if protected not in source:
        source=source.replace(marker,protected,1)
    marker='\\[\n\\begin{aligned}\nW_A&='
    protected='\\Needspace{6\\baselineskip}\n'+marker
    assert source.count(marker)==1
    if protected not in source:
        source=source.replace(marker,protected,1)
    return source

W, H = 1500, 930
INK = '#29353C'
GRAY = '#69767D'
LINE = '#CBD1D2'
BLUE, BP = '#497B9A', '#E7F0F6'
GREEN, GP = '#508374', '#EAF2EC'
OCHRE, OP = '#A98445', '#F7EEDB'
RED, RP = '#B76F69', '#F8E9E6'
PURPLE, PP = '#82769B', '#EFECF5'
PINK, PK = '#B8788D', '#F4E8EC'
FONT = 'sans-serif'
NS = '{http://www.w3.org/2000/svg}'


class Artwork:
    def __init__(self):
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" '
                      f'xmlns:xlink="http://www.w3.org/1999/xlink" '
                      f'viewBox="0 0 {W} {H}" width="{W}" height="{H}">',
                      '<rect width="1500" height="930" fill="white"/>']
        self.serial = 0

    def add(self, content):
        self.parts.append(content)

    def text(self, x, y, text, size=21, color=INK, weight='normal', anchor='start'):
        self.add(f'<text x="{x}" y="{y}" font-family="{FONT}" '
                 f'font-size="{size}" font-weight="{weight}" fill="{color}" '
                 f'text-anchor="{anchor}">{escape(text)}</text>')

    def line(self, points, color=GRAY, width=1.7, dash=None):
        pts = ' '.join(f'{x},{y}' for x,y in points)
        d = f' stroke-dasharray="{dash}"' if dash else ''
        self.add(f'<polyline points="{pts}" fill="none" stroke="{color}" '
                 f'stroke-width="{width}" stroke-linecap="round" '
                 f'stroke-linejoin="round"{d}/>')

    def arrow(self, points, color=INK, width=2, dash=None, head=8):
        self.line(points, color, width, dash)
        (x0,y0),(x1,y1) = points[-2:]
        import math
        a = math.atan2(y1-y0,x1-x0)
        c,s = math.cos(a),math.sin(a)
        pts = [(x1,y1),(x1-head*c+head*.46*s,y1-head*s-head*.46*c),
               (x1-head*c-head*.46*s,y1-head*s+head*.46*c)]
        self.add(f'<polygon points="{" ".join(f"{x},{y}" for x,y in pts)}" fill="{color}"/>')

    def rect(self, x,y,w,h,fill='white',stroke=LINE,rad=5,width=1.5,dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ''
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
                 f'rx="{rad}" fill="{fill}" stroke="{stroke}" stroke-width="{width}"{d}/>')

    def circle(self,x,y,r,fill='white',stroke=INK,width=1.5):
        self.add(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>')

    def pen_frame(self,x,y,w,h,color=LINE):
        # Sub-pixel asymmetry gives the outline a restrained pen-drawn quality.
        self.add(f'<path d="M {x+10} {y} Q {x+w/2} {y-.7} {x+w-9} {y+.5} '
                 f'Q {x+w+.5} {y+.3} {x+w} {y+10} L {x+w-.5} {y+h-10} '
                 f'Q {x+w} {y+h+.6} {x+w-10} {y+h} L {x+10} {y+h+.5} '
                 f'Q {x-.5} {y+h} {x} {y+h-9} L {x+.4} {y+10} '
                 f'Q {x-.2} {y} {x+10} {y} Z" fill="none" stroke="{color}" stroke-width="1.7"/>')

    def icon(self,name,x,y,size=29,color=INK,width=1.8):
        root=ET.fromstring((ASSET/'icons/lucide'/f'{name}.svg').read_text())
        kids=''.join(ET.tostring(child,encoding='unicode') for child in root)
        kids=kids.replace('ns0:','').replace(':ns0','')
        self.add(f'<g transform="translate({x} {y}) scale({size/24})" '
                 f'fill="none" stroke="{color}" stroke-width="{width}" '
                 f'stroke-linecap="round" stroke-linejoin="round">{kids}</g>')

    def math(self, expr,x,y,height=28,color=INK,anchor='start'):
        buff=BytesIO()
        math_to_image('$'+expr+'$',buff,prop=FontProperties(size=20),
                      format='svg',color=color,dpi=96)
        root=ET.fromstring(buff.getvalue())
        vx,vy,vw,vh=map(float,root.attrib['viewBox'].split())
        scale=height/vh
        width=vw*scale
        if anchor=='middle':x-=width/2
        elif anchor=='end':x-=width
        # Inline glyph paths: this is portable across SVG/PDF renderers that
        # resolve nested SVG <use> definitions differently.
        ids={e.attrib['id']:e for e in root.iter() if 'id' in e.attrib}
        parents={child:parent for parent in root.iter() for child in parent}
        for use in list(root.iter(NS+'use')):
            href=use.attrib.get('{http://www.w3.org/1999/xlink}href')
            glyph=copy.deepcopy(ids[href[1:]])
            glyph.attrib.pop('id',None)
            group=ET.Element(NS+'g',{'transform':use.attrib.get('transform','')})
            group.append(glyph)
            parent=parents[use]
            index=list(parent).index(use)
            parent.remove(use);parent.insert(index,group)
        for parent in root.iter():
            for child in list(parent):
                if child.tag in {NS+'defs',NS+'metadata'}:parent.remove(child)
        children=''.join(ET.tostring(c,encoding='unicode') for c in root)
        children=re.sub(r'<(/?)ns0:',r'<\1',children)
        children=re.sub(r' xmlns:ns0="[^"]+"','',children)
        self.add(f'<g transform="translate({x} {y}) scale({scale})">{children}</g>')
        return width

    def badge(self,x,y,label,color=GREEN,fill=GP,r=15):
        self.circle(x,y,r,fill,color,1.6)
        self.text(x,y+6,label,19,color,'bold','middle')

    def block(self,x,y,label,color=BLUE,fill=BP,w=34,h=29,locked=False):
        self.rect(x,y,w,h,fill,color,3,1.5)
        self.text(x+w/2,y+h*.73,label,19,color,'normal','middle')
        if locked:self.icon('lock-keyhole',x+w-13,y-11,18,INK)

    def tree(self,x,y,w=70,h=60,color=GREEN,fill=GP,stack=False):
        if stack:
            self.tree(x+12,y-10,w,h,LINE,'#FAFBFA')
            self.tree(x+6,y-5,w,h,'#A2B6AC','#F7FAF7')
        p=[(x+w/2,y),(x+w*.25,y+h*.45),(x+w*.75,y+h*.45),
           (x+w*.08,y+h),(x+w*.39,y+h),(x+w*.63,y+h),(x+w*.94,y+h)]
        for a,b in [(0,1),(0,2),(1,3),(1,4),(2,5),(2,6)]:
            self.line([p[a],p[b]],color,1.7)
        for j,(xx,yy) in enumerate(p):
            if j<3:self.circle(xx,yy,4,'white',color,1.5)
            else:self.rect(xx-5,yy-4,10,8,fill,color,1,1.2)

    def histogram(self,x,y,w=184,h=82,threshold=True):
        # Twelve bins on the actual risk grid; heights are deliberately schematic.
        ps=[.02,.04,.07,.10,.14,.19,.15,.10,.05,.03,.015,.005]
        self.line([(x,y-h-4),(x,y),(x+w+4,y)],GRAY,1.4)
        gap=w/12
        for j,p in enumerate(ps):
            fill=BP if j<8 else '#F0F1F1'
            stroke=BLUE if j<8 else '#B5BDBF'
            self.rect(x+3+j*gap,y-p*h/.20,gap-5,p*h/.20,fill,stroke,1,1)
        if threshold:
            hx=x+8*gap+1
            self.line([(hx,y-h-14),(hx,y+9)],OCHRE,1.5,'4 3')
            self.text(hx+2,y-h-4,'H = 30 s',17,OCHRE,anchor='middle')
        for idx,label in [(0,'.01'),(4,'1'),(7,'30'),(11,'600')]:
            self.text(x+(idx+.5)*gap,y+22,label,16,GRAY,anchor='middle')
        self.text(x+w/2,y+44,'time-bin edge (s)',17,GRAY,anchor='middle')

    def finish(self):
        return '\n'.join(self.parts+['</svg>'])


def panel_a(a):
    a.text(20,31,'(A)  Sessions, shared prefixes, and native inference',25,weight='bold')
    # Feedback reaches the same session, extending its exact prefix.
    a.arrow([(1435,196),(1475,196),(1475,65),(114,65),(114,99)],INK,2.1,head=9)
    a.text(950,52,'tool feedback / next user turn',20,GRAY,anchor='middle')
    a.text(21,113,'Session',19,GRAY)
    a.text(173,113,'Request prefix',19,GRAY)
    a.icon('messages-square',25,145,29,BLUE)
    a.icon('messages-square',25,214,29,BLUE)
    a.icon('messages-square',25,280,29,OCHRE)
    for y,sid,rid in [(160,'S₁','r₁'),(229,'S₁','r₂'),(295,'S₂','r₃')]:
        a.text(62,y+4,sid,21,weight='bold')
        a.text(112,y+4,rid,20,GRAY)
    for y,seq in [(142,['P','P','a','a']),
                  (211,['P','P','a','a','Δ','Δ']),
                  (277,['P','P','b','b','Δ'])]:
        for j,label in enumerate(seq):
            c,f={'P':(BLUE,BP),'a':(GREEN,GP),'b':(OCHRE,OP),'Δ':(PINK,PK)}[label]
            a.block(154+j*33,y,label,c,f,29,29)
    # Request-specific tool cues, consistent with the toolbox on the right.
    a.icon('file-code-2',358,142,26,GREEN)
    a.icon('terminal',358,212,26,GREEN)
    a.icon('database',358,278,26,OCHRE)
    a.text(154,195,'shared P',17,BLUE)
    a.line([(155,177),(155,181),(214,181),(214,177)],BLUE,1.3)
    a.text(286,260,'new Δ',17,PINK)
    # r1 is prior history, not another in-flight request of the same session.
    # Its feedback extends r2; only the current r2/r3 lanes reach FCFS.
    a.arrow([(372,174),(372,196),(318,196),(318,207)],PINK,1.4,head=6)
    a.text(365,188,'feedback',15,PINK,anchor='end')
    a.line([(391,224),(410,224),(410,290),(391,290)],GRAY,1.6)
    a.arrow([(410,224),(439,224)],INK,1.8)
    a.text(463,127,'FCFS',20,weight='bold',anchor='middle')
    a.rect(440,141,47,151,'#FBFBFA',LINE,5,1.5)
    for y,color,fill in [(152,OCHRE,OP),(190,BLUE,BP),(228,GREEN,GP)]:
        a.rect(448,y,31,26,fill,color,3,1.3)
    a.text(463,281,'head',17,GRAY,anchor='middle')
    a.arrow([(489,241),(533,241)],INK,2.2)

    a.pen_frame(538,94,630,225,'#9CB5C2')
    a.icon('cpu',556,106,25,BLUE)
    a.text(591,127,'Inference node',20,BLUE,weight='bold')
    a.text(638,161,'Prefix lookup',20,anchor='middle')
    # Mini resident-prefix tree, with a shared root rather than duplicated KV.
    for p,q in [((614,192),(588,219)),((614,192),(643,219))]:
        a.line([p,q],GRAY,1.4)
    a.block(598,178,'P',BLUE,BP,33,25)
    a.block(571,211,'a',GREEN,GP,33,25)
    a.block(633,211,'b',OCHRE,OP,33,25,locked=True)
    a.text(616,272,'reuse matched KV',17,GRAY,anchor='middle')
    a.arrow([(686,196),(1038,196)],INK,2)
    a.text(864,180,'fits',18,GRAY,anchor='middle')
    a.icon('cpu',1053,160,65,BLUE,1.6)
    a.text(1086,246,'Prefill / decode',19,anchor='middle')
    # Pressure-only control path; the badges point to the lower magnifications.
    a.arrow([(687,211),(703,211),(703,271),(732,271)],GREEN,1.9)
    a.badge(750,237,'B',GREEN,GP)
    a.tree(744,261,39,27,GREEN,GP)
    a.text(810,277,'Predict / score',20,GREEN)
    a.arrow([(949,271),(983,271)],INK,1.8)
    a.badge(991,237,'C',OCHRE,OP)
    a.icon('trash-2',983,259,25,OCHRE)
    a.text(1014,278,'Reclaim',19,OCHRE)
    a.arrow([(1094,272),(1135,272),(1135,211),(1128,211)],INK,1.8)
    a.text(865,309,'only under capacity pressure',17,GRAY,anchor='middle')
    a.arrow([(1169,196),(1207,196)],INK,2)

    a.text(1337,124,'Tools / environment',21,weight='bold',anchor='middle')
    # Pictorial toolbox: file sheets, terminal screen, database, and verification.
    for name,x,label,c in [('file-code-2',1227,'files',GREEN),
                            ('terminal',1302,'shell',INK),
                            ('database',1378,'data',OCHRE)]:
        a.icon(name,x,159,43,c,1.6)
        a.text(x+21,228,label,18,GRAY,anchor='middle')
    a.icon('file-check-2',1230,262,28,GREEN)
    a.text(1267,284,'feedback consumed',18,GRAY)
    a.text(25,338,'P: identical token prefix     a, b: session history     Δ: added context',18,GRAY)
    a.line([(20,351),(1480,351)],LINE,1.3)


def panel_b(a):
    a.text(20,386,'(B)  Learn a distribution, then score reuse',25,weight='bold')
    a.badge(960,377,'B',GREEN,GP)
    a.text(24,420,'TRAIN  ·  observed snapshots',19,GREEN,weight='bold')
    a.text(330,453,'Risk intervals',20,weight='bold')
    a.text(631,453,'Fit log-loss gradients',20,weight='bold')
    # Features appear as an illustrated list, not a text-filled generic box.
    for y,name,label in [(458,'wrench','Tool identity + phase'),
                         (498,'clock-3','Idle time + tool time'),
                         (538,'list-filter','Queued? + queue length'),
                         (578,'file-code-2','Prefix tokens + retries')]:
        a.icon(name,25,y,26,GREEN)
        a.text(63,y+21,label,20)
    a.text(25,633,'6 projects · 1,079 snapshots',18,GRAY)
    a.text(25,659,'14 encoded columns, incl. bin k',18,GRAY)
    a.arrow([(292,537),(320,537)],GREEN,1.9)
    # Explicit risk expansion: interval rows, wait labels, and masked future.
    tx,ty=330,471
    a.rect(tx,ty,232,144,'white',LINE,4,1.3)
    a.rect(tx,ty,232,29,GP,'none',2)
    for x in [tx+103,tx+147]:a.line([(x,ty),(x,ty+144)],LINE,1)
    for y in [ty+29,ty+58,ty+87,ty+116]:a.line([(tx,y),(tx+232,y)],LINE,1)
    a.text(tx+50,ty+21,'state',18,GRAY,anchor='middle')
    a.text(tx+125,ty+21,'k',18,GRAY,anchor='middle')
    a.text(tx+188,ty+21,'label',18,GRAY,anchor='middle')
    for j,(state,k,label,c) in enumerate([('x₁','1','wait',GRAY),
                                         ('x₁','2','wait',GRAY),
                                         ('x₁','3','U',BLUE),
                                         ('x₂','…','mask',GRAY)]):
        yy=ty+51+j*29
        a.text(tx+50,yy,state,19,anchor='middle')
        a.text(tx+125,yy,k,19,anchor='middle')
        a.text(tx+188,yy,label,18,c,anchor='middle')
    a.line([(tx+149,ty+119),(tx+228,ty+140)],'#ABB4B7',1.1)
    a.text(446,641,'9,229 weighted rows',18,GRAY,anchor='middle')
    a.arrow([(570,538),(603,538)],GREEN,1.9)
    # Each round is a stack of three class trees. Shapes are schematic.
    for x,label in [(612,'Round 1'),(746,'Round 2'),(881,'Round 40')]:
        a.text(x+40,482,label,18,GRAY,anchor='middle')
        a.tree(x+6,510,61,54,GREEN,GP,stack=True)
    a.text(712,541,'+',28,GREEN,anchor='middle')
    a.text(850,541,'…',28,GREEN,anchor='middle')
    a.math(r'F_m=F_{m-1}+0.05\,T_m',791,589,25,INK,'middle')
    a.text(793,637,'40 rounds · depth ≤ 2 · ≤ 4 leaves',18,GRAY,anchor='middle')
    a.text(793,660,'3 class trees / round: U, D, wait',18,GRAY,anchor='middle')
    # Model deployment is separate from the within-fit boosting iterations.
    a.arrow([(955,582),(984,582),(984,685),(248,685),(248,713)],GREEN,1.5,'5 4',7)
    a.rect(532,674,115,22,'white','none',0)
    a.text(589,692,'deploy fit',17,GREEN,anchor='middle')
    a.text(24,707,'PREDICT  ·  current state',19,BLUE,weight='bold')
    a.icon('clock-3',31,744,28,BLUE)
    a.icon('wrench',65,744,28,BLUE)
    a.icon('list-filter',99,744,28,BLUE)
    a.text(81,799,'live xᵢ',20,anchor='middle')
    a.arrow([(138,759),(196,759)],INK,1.8)
    a.tree(213,730,67,49,GREEN,GP,stack=True)
    a.text(248,809,'GBDT',19,GREEN,anchor='middle')
    a.text(246,846,'or empirical',18,GRAY,anchor='middle')
    a.arrow([(300,759),(340,759)],INK,1.8)
    # Conditional hazards must be converted before they are used as masses.
    a.text(406,731,'Hazards',20,weight='bold',anchor='middle')
    for j,(label,fill,c) in enumerate([('U',BP,BLUE),('D',RP,RED),('wait',GP,GREEN)]):
        a.rect(344,745+j*27,121,24,fill,c,2,1)
        a.text(406,764+j*27,label,18,c,anchor='middle')
    a.text(406,851,'survival recursion',17,GRAY,anchor='middle')
    a.arrow([(473,772),(511,772)],BLUE,1.8)
    a.text(614,724,'Demand mass pᵁ',20,BLUE,weight='bold',anchor='middle')
    a.histogram(519,824,188,72)
    a.arrow([(716,768),(746,768)],INK,1.8)
    a.text(861,728,'Discount & sum',20,weight='bold',anchor='middle')
    a.math(r'W_i=\sum_{r_k\leq H}e^{-r_k/\tau}\,p^U_{ik}',862,748,42,INK,'middle')
    a.text(858,826,'H = 30 s · τ = 10 s',19,GRAY,anchor='middle')
    a.text(858,852,'equal cost = 1',19,GRAY,anchor='middle')
    a.badge(967,840,'C',OCHRE,OP,13)
    a.line([(24,886),(981,886)],LINE,1.1)
    a.icon('notebook-pen',25,898,24,GREEN)
    a.text(61,918,'Optional refit: +100 mature episodes, ≥5 projects → boundary swap',19,GREEN)


def panel_c(a):
    a.line([(1003,366),(1003,919)],LINE,1.3)
    a.text(1023,386,'(C)  Score → physical KV',25,weight='bold')
    a.math(r'V_b=\max_{i\in\mathcal{O}_b}W_i\,/\,B_b',1236,407,33,INK,'middle')
    # A shared root has multiple owners, but only one physical copy.
    edges=[((1129,480),(1086,530)),((1129,480),(1217,530)),
           ((1086,530),(1052,583)),((1086,530),(1140,583))]
    for p,q in edges:a.line([p,q],GRAY,1.6)
    a.block(1108,464,'P',BLUE,BP,42,28)
    a.text(1160,480,'S₁, S₂',17,BLUE)
    a.block(1067,516,'a',GREEN,GP,38,28)
    a.block(1198,516,'b',OCHRE,OP,38,28,locked=True)
    a.text(1216,569,'active',17,GRAY,anchor='middle')
    a.block(1033,570,'b₁',RED,RP,38,28)
    a.block(1121,570,'b₂',GREEN,GP,38,28)
    a.text(1100,626,'Legal leaves only',18,GRAY,anchor='middle')
    a.text(1310,478,'leaf',18,GRAY)
    a.math(r'V_b B_b',1419,458,22,GRAY,'middle')
    a.rect(1294,496,181,38,RP,RED,4,1.5)
    a.text(1325,523,'b₁',21,RED,anchor='middle')
    a.text(1421,523,'0.07',21,RED,'bold','middle')
    a.text(1325,564,'b₂',21,anchor='middle')
    a.text(1421,564,'0.19',21,anchor='middle')
    a.text(1385,604,'lowest legal score',18,RED,anchor='middle')
    # A complete logical plan precedes native deletion.
    a.arrow([(1388,616),(1388,645),(1125,645),(1125,667)],RED,1.7)
    a.icon('trash-2',1031,674,26,RED)
    a.text(1070,695,'plan: {b₁, …}',21,RED)
    a.arrow([(1209,687),(1248,687)],GRAY,1.7)
    a.icon('git-branch',1261,673,25,GRAY)
    a.text(1297,695,'rescan until fit',19,GRAY)
    a.arrow([(1452,676),(1479,676),(1479,445),(1250,445),(1250,495)],GRAY,1.3,'4 4',7)
    a.text(1026,729,'Unbound first · support missing → LRU',18,GRAY)
    a.line([(1024,746),(1480,746)],LINE,1)
    a.icon('database',1027,761,26,BLUE)
    a.text(1066,783,'Apply completed plan once',20,weight='bold')
    # Same reserved buffer, changed occupancy. Shared P is a single cell here.
    a.text(1026,831,'before',18,GRAY)
    a.text(1026,877,'after',18,GRAY)
    cells=[('P',BLUE,BP),('a',GREEN,GP),('b₁',RED,RP),
           ('b₂',GREEN,GP),('b',OCHRE,OP),('…',GRAY,'#F3F4F3')]
    start=1105
    for y,after in [(807,False),(853,True)]:
        a.rect(start-5,y-5,369,39,'white',LINE,2,1.1)
        for j,(label,c,f) in enumerate(cells):
            x=start+j*61
            if after and j==2:
                a.rect(x,y,55,29,'white',RED,3,1.4,'3 2')
                a.text(x+27,y+21,'free',17,RED,anchor='middle')
            else:a.block(x,y,label,c,f,55,29,locked=(j==4))
    a.arrow([(1255,839),(1255,849)],RED,1.6,head=5)
    a.text(1026,916,'Shared P stored once · reserved buffer unchanged',17,GRAY)


def main():
    a=Artwork()
    panel_a(a);panel_b(a);panel_c(a)
    svg=a.finish()
    output=ASSET/'figures/leasepath_method_overview'
    output.with_suffix('.svg').write_text(svg)
    with pymupdf.open(stream=svg.encode(),filetype='svg') as doc:
        data=doc.convert_to_pdf()
    with pymupdf.open(stream=data,filetype='pdf') as doc:
        # Keep PDF geometry at the same physical scale as the editable artwork.
        doc.set_metadata({'title':'LeasePath: session workflow, learned demand, and physical KV reclaim',
                          'author':'LeasePath authors',
                          'subject':'Original schematic; initial-fit counts and frozen score parameters'})
        doc.save(output.with_suffix('.pdf'),garbage=4,deflate=True)
        doc[0].get_pixmap(matrix=pymupdf.Matrix(1.6,1.6),alpha=False).save(output.with_suffix('.png'))
    fragment = r'''\begin{figure*}[t]
\centering
\includegraphics[width=\textwidth]{leasepath_isca_v3_assets/figures/leasepath_method_overview.pdf}
\caption{''' + CAPTION + r'''}
\Description{A: three request histories include a common identical prefix P, session-specific suffixes, and added context. They enter FCFS native inference; matching prefix KV is reused, while capacity pressure invokes prediction/scoring B and reclamation C. Real tools feed results into later requests. B: tool identity, phase, idle/tool elapsed time, readiness, queue length, prefix tokens, retries, missingness, and interval index form fourteen encoded columns. The initial fit has 1079 snapshots from six development projects and 9229 weighted risk-interval rows. Waiting intervals, observed events, and censoring masks define the targets. Forty boosting rounds use three class trees per round, learning rate 0.05, depth at most two and at most four leaves. Hazards become unconditional demand masses through survival recursion; equal-cost discounted scoring uses H thirty seconds and tau ten seconds. Optional refits require one hundred additional mature episodes from at least five projects, then a boundary swap. C: maximum owner score per payload byte ranks legal unprotected leaves. Unbound leaves go first; unsupported scores use LRU. Logical removals and candidate rescans build a complete plan before it is applied once to native KV. Shared prefixes occupy one physical copy. Deletion frees used cells without shrinking the reserved buffer. The tree shapes, request histories, bars, and candidate values are schematic.}
\label{fig:method-overview}
\end{figure*}
'''
    (ASSET/'generated/method_overview.tex').write_text(fragment)
    (ASSET/'generated/method_overview_intro.tex').write_text(INTRO)
    print(output.with_suffix('.pdf'))


if __name__=='__main__':
    main()
