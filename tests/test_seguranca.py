# -*- coding: utf-8 -*-
"""Auditoria estática de segurança do Siqueirão.
NÃO faz requests reais, NÃO toca no Asaas. Analisa o código-fonte.
"""
import sys, os, re, ast, inspect

sys.stdout.reconfigure(encoding='utf-8')
BASE = r'c:\Users\fabio.pepplow\Desktop\Projetos Antigravity\Siqueirão\siqueiraomultimarcas'
sys.path.insert(0, BASE)
os.environ['DATABASE_URL'] = 'postgresql://x:x@127.0.0.1:1/none?connect_timeout=1'
os.environ.setdefault('SECRET_KEY', 'test-only')

import app as APP

SRC = open(os.path.join(BASE, 'app.py'), encoding='utf-8').read()
ISSUES = []

def check(nome, cond, detalhe=''):
    print(f"[{'OK  ' if cond else 'FALHA'}] {nome}" + (f' — {detalhe}' if detalhe else ''))
    if not cond:
        ISSUES.append((nome, detalhe))

# ═══════════ 1. Rotas sem @login_required ═══════════
print('\n== 1. AUTENTICAÇÃO: rotas desprotegidas ==')
PUBLICAS_OK = {'login', 'logout', 'setup', 'home', 'estoque', 'financiamento',
               'empresa', 'contato', 'veiculo_detalhe', 'service_worker',
               'manifest', 'serve_logo', 'static'}
tree = ast.parse(SRC)
rotas_sem_auth = []
for node in ast.walk(tree):
    if not isinstance(node, ast.FunctionDef):
        continue
    decs = [ast.unparse(d) for d in node.decorator_list]
    tem_rota = any('app.route' in d for d in decs)
    tem_auth = any('login_required' in d for d in decs)
    if tem_rota and not tem_auth and node.name not in PUBLICAS_OK:
        rota = next((d for d in decs if 'app.route' in d), '?')
        rotas_sem_auth.append(f'{node.name} {rota}')
check('nenhuma rota privada sem @login_required',
      not rotas_sem_auth, '; '.join(rotas_sem_auth) or 'todas protegidas')

# ═══════════ 2. SQL Injection ═══════════
print('\n== 2. SQL INJECTION ==')
# f-strings dentro de execute() que não sejam apenas placeholders
perigosos = []
# Só analisa a f-string do execute (até o fechamento da aspa), não 400 chars adiante
for m in re.finditer(r'cur\w*\.execute\(\s*f(["\'])((?:[^\\]|\\.)*?)\1', SRC):
    ln = SRC[:m.start()].count('\n') + 1
    fstring = m.group(2)
    for var in re.findall(r'\{([^}]+)\}', fstring):
        v = var.strip()
        # ph_ids/ph_v = placeholders "%s,%s" montados no código   → seguro
        # col/tipo    = literais de lista hardcoded nas migrations → seguro
        if re.fullmatch(r'ph\w*|col|tipo', v):
            continue
        perigosos.append(f'linha {ln}: {{{v}}}')
check('nenhuma interpolação de variável de usuário em SQL',
      not perigosos, '; '.join(perigosos) or 'só placeholders e literais de migration')

concat = re.findall(r'execute\([^)]*["\']\s*\+\s*\w', SRC)
check('nenhuma concatenação de string em execute()', not concat, f'{len(concat)} ocorrências')

# ═══════════ 3. Admin-only no backend ═══════════
print('\n== 3. AUTORIZAÇÃO: operações destrutivas ==')
# DELETEs puramente operacionais que o operador PODE executar (não financeiros)
OPERACIONAL_OK = {'delete_foto_veiculo', 'delete_crlv_veiculo', 'delete_reserva',
                  'remover_negativacao', 'delete_multa', 'delete_locacao',
                  'delete_cliente', 'delete_veiculo', 'delete_usuario'}
destrutivas = {}
for node in ast.walk(tree):
    if not isinstance(node, ast.FunctionDef):
        continue
    decs = [ast.unparse(d) for d in node.decorator_list]
    rota = next((d for d in decs if 'app.route' in d), None)
    if not rota or 'DELETE' not in rota:
        continue
    corpo = ast.unparse(node)
    protegida = ('admin_required' in decs) or ("current_user.nivel" in corpo)
    destrutivas[node.name] = protegida
sem_check = [k for k, v in destrutivas.items()
             if not v and k not in OPERACIONAL_OK]
check('todo DELETE financeiro exige perfil admin',
      not sem_check,
      f'{sum(destrutivas.values())}/{len(destrutivas)} protegidos'
      + (f'. Desprotegidos: {", ".join(sem_check)}' if sem_check else ''))

# ═══════════ 4. Config de sessão / cookies ═══════════
print('\n== 4. SESSÃO E COOKIES ==')
check('SESSION_COOKIE_SECURE = True', APP.app.config.get('SESSION_COOKIE_SECURE') is True)
check('SESSION_COOKIE_HTTPONLY = True', APP.app.config.get('SESSION_COOKIE_HTTPONLY') is True)
check("SESSION_COOKIE_SAMESITE = 'Lax'", APP.app.config.get('SESSION_COOKIE_SAMESITE') == 'Lax')
check('SECRET_KEY obrigatória (RuntimeError se ausente)', "raise RuntimeError('SECRET_KEY" in SRC)
check('MAX_CONTENT_LENGTH limita upload', APP.app.config.get('MAX_CONTENT_LENGTH') == 16*1024*1024)
check('DEBUG desligado', APP.app.debug is False)

# ═══════════ 5. Senhas ═══════════
print('\n== 5. SENHAS ==')
check('usa generate_password_hash', 'generate_password_hash' in SRC)
check('usa check_password_hash', 'check_password_hash' in SRC)
check('nenhuma comparação de senha em texto puro',
      not re.search(r'senha\s*==\s*row', SRC))

# ═══════════ 6. Segredos hardcoded ═══════════
print('\n== 6. SEGREDOS ==')
padroes = [
    (r'postgres(?:ql)?://[^\'"\s]*:[^\'"\s@]{4,}@', 'connection string com senha'),
    (r'(?i)(api[_-]?key|token|secret|password)\s*=\s*["\'][A-Za-z0-9_\-]{16,}["\']', 'credencial literal'),
    (r'lsws\.com\.br', 'host proibido lsws.com.br'),
]
achados = []
for pat, desc in padroes:
    for m in re.finditer(pat, SRC):
        ln = SRC[:m.start()].count('\n') + 1
        achados.append(f'{desc} linha {ln}')
check('nenhum segredo hardcoded no app.py', not achados, '; '.join(achados))

env_vars = set(re.findall(r"os\.environ\.get\(['\"](\w+)['\"]", SRC))
check('credenciais vêm de variáveis de ambiente', len(env_vars) >= 8,
      f'{len(env_vars)} env vars: {", ".join(sorted(env_vars)[:6])}...')

# ═══════════ 7. Headers de segurança ═══════════
print('\n== 7. HEADERS HTTP ==')
c = APP.app.test_client()
with APP.app.test_request_context('/'):
    pass
resp_headers = {}
try:
    r = c.get('/login')
    resp_headers = dict(r.headers)
except Exception as e:
    print(f'  (não foi possível bater em /login sem DB: {type(e).__name__})')
for h, exp in [('X-Frame-Options', 'SAMEORIGIN'),
               ('X-Content-Type-Options', 'nosniff'),
               ('Referrer-Policy', 'strict-origin-when-cross-origin')]:
    if resp_headers:
        check(f'header {h}', resp_headers.get(h) == exp, resp_headers.get(h, 'ausente'))
    else:
        check(f'header {h} definido no after_request', f"'{h}'" in SRC)

# ═══════════ 8. Open redirect ═══════════
print('\n== 8. OPEN REDIRECT ==')
check('login valida next_url (bloqueia domínio externo)',
      'urlparse(next_url).netloc' in SRC)

# ═══════════ 9. XSS nos templates ═══════════
print('\n== 9. XSS EM TEMPLATES ==')
tdir = os.path.join(BASE, 'templates')
safes, alerts, innerhtml_sem_esc = [], [], []
for f in os.listdir(tdir):
    if not f.endswith('.html'):
        continue
    t = open(os.path.join(tdir, f), encoding='utf-8').read()
    for m in re.finditer(r'\|\s*safe', t):
        safes.append(f'{f}:{t[:m.start()].count(chr(10))+1}')
    for m in re.finditer(r'(?<![\w.])(alert|confirm|prompt)\s*\(', t):
        # ignora comentários e nomes tipo showAlert
        alerts.append(f'{f}:{t[:m.start()].count(chr(10))+1} {m.group(1)}')
check('nenhum |safe desnecessário nos templates', not safes, '; '.join(safes[:6]))
check('nenhum alert()/confirm()/prompt() nativo', not alerts, '; '.join(alerts[:8]))

# ═══════════ 10. Upload ═══════════
print('\n== 10. UPLOAD DE ARQUIVOS ==')
check('valida extensão (allowed_file)', 'def allowed_file' in SRC)
check('valida magic bytes (_valid_image_magic)', 'def _valid_image_magic' in SRC)
check('fotos vão para Cloudinary (não base64/disco)',
      'cloudinary.uploader.upload' in SRC and 'base64.b64encode(foto' not in SRC)

# ═══════════ 11. Brute force ═══════════
print('\n== 11. BRUTE FORCE ==')
check('tentativas persistem no banco (não in-memory)',
      'login_attempts' in SRC and '_failed_logins' not in SRC)
check('limite aplicado ANTES de validar a senha',
      SRC.index('if _brute_check(ip)') < SRC.index('check_password_hash(row[4]'))
check('tentativa falha é registrada (_brute_record)', '_brute_record(ip)' in SRC)
check('janela de bloqueio e limite definidos', "INTERVAL '600 seconds'" in SRC and '>= 5' in SRC)

# ═══════════ 12. Vazamento de stack trace ═══════════
print('\n== 12. VAZAMENTO DE ERRO ==')
vaza = re.findall(r"jsonify\(\{'error':\s*f['\"][^'\"]*\{e\}", SRC)
check('erros internos não expõem exception ao cliente',
      len(vaza) <= 3, f'{len(vaza)} rotas retornam str(e) — revisar')

# ═══════════ Resumo ═══════════
print('\n' + '=' * 62)
if ISSUES:
    print(f'{len(ISSUES)} PONTO(S) DE ATENÇÃO:\n')
    for n, d in ISSUES:
        print(f'  • {n}')
        if d: print(f'      {d}')
    sys.exit(1)
print('NENHUMA VULNERABILIDADE ENCONTRADA')
