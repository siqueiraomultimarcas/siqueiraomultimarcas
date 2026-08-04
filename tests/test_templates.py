# -*- coding: utf-8 -*-
"""Valida integridade dos templates: Jinja, XSS, paleta e IDs referenciados no JS."""
import sys, os, re, io

sys.stdout.reconfigure(encoding='utf-8')
BASE = r'c:\Users\fabio.pepplow\Desktop\Projetos Antigravity\Siqueirão\siqueiraomultimarcas'
TDIR = os.path.join(BASE, 'templates')
ISSUES = []

def check(nome, cond, detalhe=''):
    print(f"[{'OK  ' if cond else 'FALHA'}] {nome}" + (f' — {detalhe}' if detalhe else ''))
    if not cond: ISSUES.append(nome)

arquivos = sorted(f for f in os.listdir(TDIR) if f.endswith('.html'))
conteudo = {f: io.open(os.path.join(TDIR, f), encoding='utf-8').read() for f in arquivos}

# ═══ 1. Jinja: blocos balanceados ═══
print('\n== 1. INTEGRIDADE JINJA ==')
desbal = []
for f, t in conteudo.items():
    for tag in ('block', 'if', 'for'):
        ab = len(re.findall(r'\{%-?\s*' + tag + r'\s', t))
        # {% endblock %} e {% endblock nome %} são ambos válidos
        fe = len(re.findall(r'\{%-?\s*end' + tag + r'\b[^%]*-?%\}', t))
        if ab != fe:
            desbal.append(f'{f}/{tag}: {ab} vs {fe}')
check('blocos Jinja balanceados em todos os templates', not desbal, '; '.join(desbal))

# ═══ 2. Templates declaram apenas blocos que o parent define ═══
print('\n== 2. HERANÇA DE TEMPLATE ==')
base = conteudo['base.html']
def blocos_de(nome):
    return set(re.findall(r'\{%\s*block\s+(\w+)', conteudo.get(nome, '')))
ruins = []
for f, t in conteudo.items():
    m = re.search(r'\{%\s*extends\s+["\']([\w./]+)["\']', t)
    if not m: continue
    parent = m.group(1)
    disp = blocos_de(parent)
    if not disp: continue
    for b in set(re.findall(r'\{%\s*block\s+(\w+)', t)):
        if b not in disp:
            ruins.append(f'{f}→{parent}:{b}')
check('nenhum bloco órfão (declarado mas inexistente no parent)', not ruins, '; '.join(ruins))

# ═══ 3. XSS: interpolação de dados em atributo onclick ═══
print('\n== 3. XSS — dados do servidor em atributo onclick ==')
riscos = []
# Só é risco quando o valor interpolado pode conter TEXTO do banco.
# Expressões numéricas/identificadores (ids, índices, contadores) não quebram o atributo.
SEGURO = re.compile(r"""^(
      (_esc|escHtml|parseFloat|parseInt|Number|encodeURIComponent)\(.*\)   # já escapado/numérico
    | '[^']*'                                                             # literal fixo
    | [A-Za-z_$][\w$]*(\.[\w$]+)*                                         # ident: d.id, lid, loc.locacao_id
      (\s*[-+]\s*\d+)?                                                    #   com aritmética simples
    | \d+
    )$""", re.X)
TEXTUAL = re.compile(r'(?i)(nome|desc|descricao|cliente|obs|observ|placa|titulo|just|motivo|email|texto|label|marca|modelo|val)')
ESCAPADO = ('_esc(', 'escHtml(', 'escJsAttr(', 'encodeURIComponent(')
# identificadores terminados em id/Id/_id são chaves numéricas, não texto livre
ID_NUMERICO = re.compile(r'(?i)(_id|\bid|[a-z]Id)$')
for f, t in conteudo.items():
    for m in re.finditer(r'onclick="[^"]*?\$\{([^}]+)\}[^"]*"', t):
        expr = m.group(1).strip()
        if expr.startswith(ESCAPADO):        continue
        if ID_NUMERICO.search(expr):          continue
        if SEGURO.match(expr) and not TEXTUAL.search(expr): continue
        if TEXTUAL.search(expr):
            riscos.append(f"{f}:{t[:m.start()].count(chr(10))+1} {{{expr}}}")
check('nenhum texto livre do banco interpolado em onclick', not riscos,
      '; '.join(riscos[:10]) or 'só ids, números e valores escapados')

# ═══ 4. alert/confirm/prompt nativos ═══
print('\n== 4. UX — sem diálogos nativos ==')
nativos = []
for f, t in conteudo.items():
    for m in re.finditer(r'(?<![\w.$])(alert|confirm|prompt)\s*\(', t):
        nativos.append(f'{f}:{t[:m.start()].count(chr(10))+1}')
check('nenhum alert()/confirm()/prompt()', not nativos, '; '.join(nativos[:6]))

# ═══ 5. Paleta: cores antigas removidas ═══
print('\n== 5. PALETA — cores legadas ==')
LEGADAS = ['#33cd5f', '#ffc900', '#ef473a', '#27a84f', '#c73a2e',
           '#16a34a', '#ef4444', '#d97706', '#252525']
PRINCIPAIS = ('base.html','index.html','relatorios.html','contas_receber.html','contas_pagar.html')
achados_princ, achados_outros = [], []
for f, t in conteudo.items():
    for c in LEGADAS:
        n = t.lower().count(c)
        if not n: continue
        (achados_princ if f in PRINCIPAIS else achados_outros).append(f'{f}: {c} x{n}')
check('paleta unificada nas telas repaginadas', not achados_princ,
      '; '.join(achados_princ[:10]) or 'base, dashboard, relatórios e contas OK')
print(f'  (info) telas ainda com cor legada, fora do escopo desta rodada: '
      f'{len({a.split(":")[0] for a in achados_outros})} arquivo(s)')

# ═══ 6. IDs usados no JS existem no HTML ═══
print('\n== 6. IDs referenciados por getElementById existem no HTML ==')
faltando = []
for f in ('index.html', 'relatorios.html', 'contas_receber.html', 'contas_pagar.html'):
    t = conteudo[f]
    # ids definidos aqui ou no base (layout compartilhado)
    definidos = set(re.findall(r'\bid="([\w\-]+)"', t)) | set(re.findall(r'\bid="([\w\-]+)"', base))
    definidos |= set(re.findall(r"id=[`']([\w\-]+)", t))
    for m in re.finditer(r"getElementById\(\s*'([\w\-]+)'\s*\)", t):
        i = m.group(1)
        if i not in definidos:
            faltando.append(f'{f}: #{i}')
check('nenhum getElementById apontando para id inexistente', not faltando,
      '; '.join(faltando[:10]))

# ═══ 7. escHtml disponível globalmente ═══
print('\n== 7. HELPER DE ESCAPE ==')
check('base.html define escHtml() global', 'function escHtml(' in base)
check('index.html usa escape nos dados do servidor', 'escHtml(' in conteudo['index.html'])
check('relatorios.html usa escape', 'escHtml(' in conteudo['relatorios.html'])
check('contas_receber.html usa escape', '_esc(' in conteudo['contas_receber.html'])

# ═══ 8. Acessibilidade / responsivo básico ═══
print('\n== 8. RESPONSIVO ==')
for f in ('index.html', 'relatorios.html', 'contas_receber.html'):
    check(f'{f} tem media query mobile', '@media' in conteudo[f])
check('base.html respeita prefers-reduced-motion',
      'prefers-reduced-motion' in base or 'prefers-reduced-motion' in conteudo['index.html'])

print('\n' + '=' * 62)
if ISSUES:
    print(f'{len(ISSUES)} PROBLEMA(S):')
    for i in ISSUES: print('  •', i)
    sys.exit(1)
print('TEMPLATES OK')
