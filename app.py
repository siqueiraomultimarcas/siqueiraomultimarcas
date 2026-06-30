from flask import Flask, render_template, request, jsonify, redirect, url_for, send_from_directory, Response
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import cloudinary
import cloudinary.uploader
import json
import os
import base64
import re
import io
from contextlib import contextmanager
from datetime import datetime, date as _date, timedelta
from dotenv import load_dotenv
import requests
import pdfplumber

load_dotenv()

app = Flask(__name__)
_secret = os.environ.get('SECRET_KEY')
if not _secret:
    raise RuntimeError('SECRET_KEY não configurada — defina a variável de ambiente antes de iniciar.')
app.secret_key = _secret
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

cloudinary.config(
    cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME'),
    api_key=os.environ.get('CLOUDINARY_API_KEY'),
    api_secret=os.environ.get('CLOUDINARY_API_SECRET'),
)

# ==================== FLASK-LOGIN ====================

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Faça login para acessar o sistema.'
login_manager.login_message_category = 'warning'


class User(UserMixin):
    def __init__(self, id, nome, email, nivel):
        self.id = id
        self.nome = nome
        self.email = email
        self.nivel = nivel


@login_manager.user_loader
def load_user(user_id):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            'SELECT id, nome, email, nivel FROM usuarios WHERE id = %s AND ativo = TRUE',
            (user_id,)
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return User(*row)
    except Exception:
        pass
    return None

# ==================== BANCO DE DADOS ====================

def get_conn():
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise RuntimeError('DATABASE_URL não configurada. Crie um arquivo .env com a connection string do Neon.')
    return psycopg2.connect(url)


@contextmanager
def _db():
    conn = get_conn()
    cur = conn.cursor()
    try:
        yield conn, cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try: cur.close()
        except Exception: pass
        try: conn.close()
        except Exception: pass


def _serialize_val(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, _date):
        return v.isoformat()
    return v

def rows_to_dict(cursor):
    cols = [d[0] for d in cursor.description]
    return [{c: _serialize_val(v) for c, v in zip(cols, row)} for row in cursor.fetchall()]


def row_to_dict(cursor):
    cols = [d[0] for d in cursor.description]
    row = cursor.fetchone()
    return {c: _serialize_val(v) for c, v in zip(cols, row)} if row else None


_db_initialized = False

def init_db():
    global _db_initialized
    if _db_initialized:
        return
    conn = get_conn()
    cur = conn.cursor()

    cur.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            nome VARCHAR(100) NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL,
            senha_hash VARCHAR(255) NOT NULL,
            nivel VARCHAR(20) DEFAULT 'operador',
            ativo BOOLEAN DEFAULT TRUE,
            data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS clientes (
            id SERIAL PRIMARY KEY,
            nome TEXT NOT NULL,
            cpf TEXT UNIQUE,
            cnh TEXT,
            telefone TEXT,
            email TEXT,
            endereco TEXT,
            cidade TEXT,
            estado TEXT,
            status TEXT DEFAULT 'ativo',
            observacoes TEXT,
            data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS veiculos (
            id SERIAL PRIMARY KEY,
            placa TEXT UNIQUE NOT NULL,
            marca TEXT NOT NULL,
            modelo TEXT NOT NULL,
            ano INTEGER,
            cor TEXT,
            categoria TEXT,
            diaria NUMERIC(10,2),
            km_atual INTEGER DEFAULT 0,
            status TEXT DEFAULT 'disponivel',
            combustivel TEXT,
            foto TEXT,
            observacoes TEXT,
            data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS manutencoes (
            id SERIAL PRIMARY KEY,
            veiculo_id INTEGER REFERENCES veiculos(id),
            tipo TEXT NOT NULL,
            descricao TEXT,
            data_manutencao DATE,
            km_manutencao INTEGER,
            custo NUMERIC(10,2),
            oficina TEXT,
            proxima_manutencao_km INTEGER,
            proxima_manutencao_data DATE,
            status TEXT DEFAULT 'concluida'
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS locacoes (
            id SERIAL PRIMARY KEY,
            veiculo_id INTEGER REFERENCES veiculos(id),
            cliente_id INTEGER REFERENCES clientes(id),
            data_inicio DATE,
            data_fim DATE,
            data_devolucao_real DATE,
            diaria NUMERIC(10,2),
            total NUMERIC(10,2),
            km_saida INTEGER,
            km_retorno INTEGER,
            status TEXT DEFAULT 'ativa',
            checklist TEXT,
            fotos_saida TEXT,
            fotos_retorno TEXT,
            observacoes TEXT
        )
    ''')
    # Migração: adiciona colunas novas se a tabela já existir sem elas
    for col, tipo in [('checklist','TEXT'), ('fotos_saida','TEXT'), ('fotos_retorno','TEXT'), ('data_devolucao_real','DATE')]:
        cur.execute(f"ALTER TABLE locacoes ADD COLUMN IF NOT EXISTS {col} {tipo}")
    cur.execute("ALTER TABLE veiculos ADD COLUMN IF NOT EXISTS renavam TEXT")
    cur.execute("ALTER TABLE veiculos ADD COLUMN IF NOT EXISTS chassi TEXT")
    cur.execute("ALTER TABLE veiculos ADD COLUMN IF NOT EXISTS ano_fabricacao INTEGER")
    cur.execute("ALTER TABLE veiculos ADD COLUMN IF NOT EXISTS potencia TEXT")
    cur.execute("ALTER TABLE veiculos ADD COLUMN IF NOT EXISTS versao TEXT")
    cur.execute("ALTER TABLE veiculos ADD COLUMN IF NOT EXISTS crlv_url TEXT")
    cur.execute("ALTER TABLE veiculos ADD COLUMN IF NOT EXISTS valor_fipe NUMERIC(12,2)")
    cur.execute("ALTER TABLE veiculos ADD COLUMN IF NOT EXISTS codigo_fipe TEXT")
    cur.execute("ALTER TABLE veiculos ADD COLUMN IF NOT EXISTS referencia_fipe TEXT")
    cur.execute("ALTER TABLE veiculos ADD COLUMN IF NOT EXISTS valor_compra NUMERIC(12,2)")
    cur.execute("ALTER TABLE multas ADD COLUMN IF NOT EXISTS numero_auto TEXT")
    cur.execute("ALTER TABLE multas ADD COLUMN IF NOT EXISTS data_pagamento DATE")
    cur.execute("ALTER TABLE multas ADD COLUMN IF NOT EXISTS tipo_notificacao TEXT DEFAULT 'multa'")
    cur.execute("ALTER TABLE multas ADD COLUMN IF NOT EXISTS data_limite_defesa DATE")
    cur.execute("ALTER TABLE multas ADD COLUMN IF NOT EXISTS numero_renainf TEXT")
    cur.execute("ALTER TABLE multas ADD COLUMN IF NOT EXISTS hora_infracao TEXT")
    for col, tipo in [('cep','TEXT'), ('bairro','TEXT'), ('nome_fantasia','TEXT'), ('data_fundacao','DATE'), ('situacao_receita','TEXT'), ('responsavel_principal','TEXT')]:
        cur.execute(f"ALTER TABLE fornecedores ADD COLUMN IF NOT EXISTS {col} {tipo}")
    for col, tipo in [('numero_pedido','TEXT'), ('fornecedor_id','INTEGER'), ('itens_json','TEXT')]:
        cur.execute(f"ALTER TABLE manutencoes ADD COLUMN IF NOT EXISTS {col} {tipo}")
    cur.execute("ALTER TABLE clientes ADD COLUMN IF NOT EXISTS cep TEXT")
    cur.execute("ALTER TABLE clientes ADD COLUMN IF NOT EXISTS bairro TEXT")

    cur.execute('''
        CREATE TABLE IF NOT EXISTS abastecimentos (
            id SERIAL PRIMARY KEY,
            veiculo_id INTEGER REFERENCES veiculos(id),
            data_abastecimento DATE,
            litros NUMERIC(10,2),
            valor_litro NUMERIC(10,3),
            total NUMERIC(10,2),
            km_abastecimento INTEGER
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS multas (
            id SERIAL PRIMARY KEY,
            veiculo_id INTEGER REFERENCES veiculos(id),
            motorista_id INTEGER REFERENCES clientes(id),
            data_infracao DATE,
            descricao TEXT,
            valor NUMERIC(10,2),
            local_infracao TEXT,
            pontos INTEGER,
            status TEXT DEFAULT 'pendente',
            observacoes TEXT
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS tipos_fornecedor (
            id SERIAL PRIMARY KEY,
            nome TEXT NOT NULL UNIQUE,
            descricao TEXT,
            data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS fornecedores (
            id SERIAL PRIMARY KEY,
            nome TEXT NOT NULL,
            cnpj TEXT,
            cpf TEXT,
            telefone TEXT,
            email TEXT,
            endereco TEXT,
            cidade TEXT,
            estado TEXT,
            tipo_id INTEGER REFERENCES tipos_fornecedor(id),
            responsavel TEXT,
            status TEXT DEFAULT 'ativo',
            observacoes TEXT,
            data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    for tipo in ['Oficina', 'Lataria', 'Elétrica', 'Seguradora', 'Combustível', 'Peças', 'Seguro', 'Outros']:
        cur.execute("INSERT INTO tipos_fornecedor (nome) VALUES (%s) ON CONFLICT (nome) DO NOTHING", (tipo,))

    cur.execute('''
        CREATE TABLE IF NOT EXISTS pagamentos_locacao (
            id SERIAL PRIMARY KEY,
            locacao_id INTEGER REFERENCES locacoes(id) ON DELETE CASCADE,
            semana_numero INTEGER NOT NULL,
            data_inicio DATE,
            data_fim DATE,
            valor_previsto NUMERIC(10,2),
            valor_pago NUMERIC(10,2),
            desconto NUMERIC(10,2) DEFAULT 0,
            justificativa_desconto TEXT,
            status TEXT DEFAULT 'pendente',
            data_pagamento DATE,
            observacoes TEXT,
            data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (locacao_id, semana_numero)
        )
    ''')
    cur.execute("ALTER TABLE pagamentos_locacao ADD COLUMN IF NOT EXISTS asaas_id TEXT")
    cur.execute("ALTER TABLE pagamentos_locacao ADD COLUMN IF NOT EXISTS asaas_link TEXT")
    cur.execute("ALTER TABLE pagamentos_locacao ADD COLUMN IF NOT EXISTS asaas_status TEXT")
    cur.execute("ALTER TABLE pagamentos_locacao ADD COLUMN IF NOT EXISTS parcela_num INTEGER DEFAULT 1")
    cur.execute("ALTER TABLE pagamentos_locacao ADD COLUMN IF NOT EXISTS total_parcelas INTEGER DEFAULT 1")
    # Normalizar status legado 'finalizada' → 'concluida'
    cur.execute("UPDATE locacoes SET status='concluida' WHERE status='finalizada'")

    cur.execute('''
        CREATE TABLE IF NOT EXISTS cobrancas_avulsas (
            id SERIAL PRIMARY KEY,
            cliente_id INTEGER REFERENCES clientes(id),
            descricao TEXT NOT NULL,
            valor NUMERIC(10,2) NOT NULL,
            data_vencimento DATE,
            status TEXT DEFAULT 'pendente',
            data_recebimento DATE,
            observacoes TEXT,
            data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute("ALTER TABLE cobrancas_avulsas ADD COLUMN IF NOT EXISTS asaas_id TEXT")
    cur.execute("ALTER TABLE cobrancas_avulsas ADD COLUMN IF NOT EXISTS asaas_link TEXT")
    cur.execute("ALTER TABLE cobrancas_avulsas ADD COLUMN IF NOT EXISTS asaas_status TEXT")
    cur.execute("ALTER TABLE cobrancas_avulsas ADD COLUMN IF NOT EXISTS desconto NUMERIC(10,2) DEFAULT 0")
    cur.execute("ALTER TABLE cobrancas_avulsas ADD COLUMN IF NOT EXISTS justificativa_desconto TEXT")
    cur.execute("ALTER TABLE multas ADD COLUMN IF NOT EXISTS desconto NUMERIC(10,2) DEFAULT 0")
    cur.execute("ALTER TABLE multas ADD COLUMN IF NOT EXISTS justificativa_desconto TEXT")
    cur.execute("ALTER TABLE multas ADD COLUMN IF NOT EXISTS asaas_id TEXT")
    cur.execute("ALTER TABLE multas ADD COLUMN IF NOT EXISTS asaas_link TEXT")
    cur.execute("ALTER TABLE multas ADD COLUMN IF NOT EXISTS asaas_status TEXT")
    cur.execute("ALTER TABLE locacoes ADD COLUMN IF NOT EXISTS frequencia_cobranca TEXT DEFAULT 'avulso'")
    cur.execute("ALTER TABLE pagamentos_locacao ADD COLUMN IF NOT EXISTS observacao TEXT")
    cur.execute("ALTER TABLE multas ADD COLUMN IF NOT EXISTS observacao TEXT")

    # ── Índices — evita Seq Scan em JOINs e filtros frequentes ──────────────
    cur.execute("CREATE INDEX IF NOT EXISTS idx_locacoes_veiculo   ON locacoes(veiculo_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_locacoes_cliente   ON locacoes(cliente_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_locacoes_status    ON locacoes(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_locacoes_data_ini  ON locacoes(data_inicio)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_locacoes_data_fim  ON locacoes(data_fim)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pag_locacao_id     ON pagamentos_locacao(locacao_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pag_status         ON pagamentos_locacao(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pag_data_fim       ON pagamentos_locacao(data_fim)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_manut_veiculo      ON manutencoes(veiculo_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_manut_status       ON manutencoes(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_multas_veiculo     ON multas(veiculo_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_multas_motorista   ON multas(motorista_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_multas_status      ON multas(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_abast_veiculo      ON abastecimentos(veiculo_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_avulsas_cliente    ON cobrancas_avulsas(cliente_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_avulsas_status     ON cobrancas_avulsas(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_veiculos_placa_up  ON veiculos(UPPER(placa))")

    conn.commit()
    cur.close()
    conn.close()
    _db_initialized = True
    print('Banco de dados inicializado com sucesso!')

# ==================== ASAAS ====================

ASAAS_BASE = 'https://api.asaas.com/v3'

def _asaas_req(method, path, data=None):
    token = os.environ.get('ASAAS_TOKEN', '')
    headers = {'access_token': token, 'Content-Type': 'application/json', 'User-Agent': 'SiqueiraoSystem/1.0'}
    resp = requests.request(method, ASAAS_BASE + path, headers=headers, json=data, timeout=15)
    try:
        return resp.json(), resp.status_code
    except Exception:
        return {}, resp.status_code

def _asaas_get_or_create_customer(cpf, nome, email='', telefone=''):
    cpf_clean = re.sub(r'\D', '', cpf or '')
    if not cpf_clean:
        return None, 'CPF do cliente não informado'
    result, status = _asaas_req('GET', f'/customers?cpfCnpj={cpf_clean}&limit=1')
    if status == 200 and result.get('data'):
        existing = result['data'][0]
        cust_id  = existing['id']
        # Atualiza email/telefone se o cliente já existia sem eles
        update = {}
        if email    and not existing.get('email'):       update['email']       = email
        if telefone and not existing.get('mobilePhone'): update['mobilePhone'] = re.sub(r'\D', '', telefone)
        if update:
            _asaas_req('PUT', f'/customers/{cust_id}', update)
        return cust_id, None
    payload = {'name': nome, 'cpfCnpj': cpf_clean}
    if email:    payload['email']       = email
    if telefone: payload['mobilePhone'] = re.sub(r'\D', '', telefone)
    result, status = _asaas_req('POST', '/customers', payload)
    if status in (200, 201) and result.get('id'):
        return result['id'], None
    errs = result.get('errors', [{}])
    return None, errs[0].get('description', 'Erro ao criar cliente no Asaas')

@app.route('/api/asaas/gerar-cobranca', methods=['POST'])
@login_required
def asaas_gerar_cobranca():
    data      = request.json
    cpf       = data.get('cpf', '')
    nome      = data.get('nome', '')
    email     = data.get('email', '')
    telefone  = data.get('telefone', '')
    billing   = data.get('billing_type', 'PIX')   # PIX ou BOLETO
    valor     = float(data.get('valor') or 0)
    due_date  = data.get('data_vencimento')        # YYYY-MM-DD
    descricao = data.get('descricao', 'Locação de veículo — Siqueirão Multimarcas')

    if valor <= 0 or not due_date:
        return jsonify({'error': 'Valor ou data de vencimento inválidos'}), 400

    customer_id, err = _asaas_get_or_create_customer(cpf, nome, email, telefone)
    if err:
        return jsonify({'error': err}), 500

    charge, status = _asaas_req('POST', '/payments', {
        'customer':    customer_id,
        'billingType': billing,
        'dueDate':     due_date,
        'value':       round(valor, 2),
        'description': descricao,
    })
    if status not in (200, 201):
        errs = charge.get('errors', [{}])
        return jsonify({'error': errs[0].get('description', 'Erro ao criar cobrança no Asaas')}), 500

    result = {
        'asaas_id':    charge.get('id'),
        'invoice_url': charge.get('invoiceUrl', ''),
        'status':      charge.get('status', ''),
        'bank_slip_url': charge.get('bankSlipUrl', ''),
    }
    if billing == 'PIX':
        pix, _ = _asaas_req('GET', f'/payments/{charge["id"]}/pixQrCode')
        result['pix_qrcode']  = pix.get('encodedImage', '')
        result['pix_payload'] = pix.get('payload', '')

    return jsonify(result)

@app.route('/api/asaas/status/<asaas_id>', methods=['GET'])
@login_required
def asaas_check_status(asaas_id):
    charge, status = _asaas_req('GET', f'/payments/{asaas_id}')
    if status != 200:
        return jsonify({'error': 'Cobrança não encontrada'}), 404
    label_map = {
        'PENDING':    ('Aguardando', '#f59e0b'),
        'RECEIVED':   ('Recebido',   '#16a34a'),
        'CONFIRMED':  ('Confirmado', '#16a34a'),
        'OVERDUE':    ('Vencido',    '#dc2626'),
        'REFUNDED':   ('Estornado',  '#6b7280'),
        'CANCELLED':  ('Cancelado',  '#6b7280'),
    }
    st = charge.get('status', '')
    lbl, cor = label_map.get(st, (st, '#64748b'))
    return jsonify({
        'status':      st,
        'label':       lbl,
        'cor':         cor,
        'invoice_url': charge.get('invoiceUrl', ''),
        'bank_slip_url': charge.get('bankSlipUrl', ''),
        'value':       charge.get('value'),
        'due_date':    charge.get('dueDate'),
    })

# ==================== PWA ====================

@app.route('/sw.js')
def service_worker():
    return app.send_static_file('sw.js'), 200, {'Content-Type': 'application/javascript'}

@app.route('/manifest.json')
def manifest():
    return app.send_static_file('manifest.json'), 200, {'Content-Type': 'application/manifest+json'}

# ==================== ROTAS DE AUTENTICAÇÃO ====================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')
        lembrar = request.form.get('lembrar') == 'on'

        try:
            with _db() as (conn, cur):
                cur.execute(
                    'SELECT id, nome, email, nivel, senha_hash FROM usuarios WHERE email = %s AND ativo = TRUE',
                    (email,)
                )
                row = cur.fetchone()
        except Exception as e:
            return render_template('login.html', erro=f'Erro de conexão com o banco: {e}')

        if row and check_password_hash(row[4], senha):
            user = User(row[0], row[1], row[2], row[3])
            login_user(user, remember=lembrar)
            next_url = request.args.get('next') or ''
            from urllib.parse import urlparse
            if urlparse(next_url).netloc:  # URL absoluta → rejeita (open redirect)
                next_url = ''
            return redirect(next_url or url_for('index'))

        return render_template('login.html', erro='E-mail ou senha incorretos.')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    """Cria o primeiro usuário admin. Só funciona se não houver nenhum usuário."""
    try:
        with _db() as (conn, cur):
            cur.execute('SELECT COUNT(*) FROM usuarios')
            total = cur.fetchone()[0]
    except Exception as e:
        return f'Erro ao conectar ao banco: {e}', 500

    if total > 0:
        return redirect(url_for('login'))

    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')

        if not nome or not email or not senha:
            return render_template('setup.html', erro='Preencha todos os campos.')

        try:
            with _db() as (conn, cur):
                cur.execute(
                    'INSERT INTO usuarios (nome, email, senha_hash, nivel) VALUES (%s, %s, %s, %s)',
                    (nome, email, generate_password_hash(senha), 'admin')
                )
        except Exception as e:
            return render_template('setup.html', erro=f'Erro ao criar usuário: {e}')

        return redirect(url_for('login'))

    return render_template('setup.html')

# ==================== ROTAS DE PÁGINAS ====================

@app.route('/')
@login_required
def index():
    return render_template('index.html')


@app.route('/clientes')
@login_required
def clientes():
    return render_template('clientes.html')


@app.route('/veiculos')
@login_required
def veiculos():
    return render_template('veiculos.html')


@app.route('/manutencoes')
@login_required
def manutencoes():
    return render_template('manutencoes.html')


@app.route('/locacoes')
@login_required
def locacoes():
    return render_template('locacoes.html')


@app.route('/abastecimentos')
@login_required
def abastecimentos():
    return render_template('abastecimentos.html')


@app.route('/multas')
@login_required
def multas():
    return render_template('multas.html')


@app.route('/fornecedores')
@login_required
def fornecedores():
    return render_template('fornecedores.html')


@app.route('/operacional')
@login_required
def operacional():
    return render_template('operacional.html')


@app.route('/api/operacional')
@login_required
def get_operacional():
    from datetime import date, timedelta
    hoje = date.today()
    proximos7 = hoje + timedelta(days=7)
    proximos10 = hoje + timedelta(days=10)

    with _db() as (conn, cur):
        cur.execute('''
            SELECT l.id, v.placa, v.marca, v.modelo,
                   c.nome AS cliente, COALESCE(c.telefone,'') AS telefone,
                   l.data_inicio::text AS data_inicio, l.data_fim::text AS data_fim,
                   COALESCE(l.total, 0) AS total
            FROM locacoes l
            JOIN veiculos v ON v.id = l.veiculo_id
            JOIN clientes c ON c.id = l.cliente_id
            WHERE l.status = %s AND l.data_fim = %s
            ORDER BY c.nome
        ''', ('ativa', hoje))
        devolucoes_hoje = rows_to_dict(cur)

        cur.execute('''
            SELECT l.id, v.placa, v.marca, v.modelo,
                   c.nome AS cliente, COALESCE(c.telefone,'') AS telefone,
                   l.data_inicio::text AS data_inicio, l.data_fim::text AS data_fim,
                   COALESCE(l.total, 0) AS total
            FROM locacoes l
            JOIN veiculos v ON v.id = l.veiculo_id
            JOIN clientes c ON c.id = l.cliente_id
            WHERE l.status = %s AND l.data_fim > %s AND l.data_fim <= %s
            ORDER BY l.data_fim, c.nome
        ''', ('ativa', hoje, proximos7))
        devolucoes_semana = rows_to_dict(cur)

        cur.execute('''
            SELECT 'contrato' AS tipo, pl.id,
                   c.nome AS cliente, COALESCE(c.telefone,'') AS telefone,
                   pl.data_fim::text AS data_vencimento,
                   COALESCE(pl.valor_previsto,0) - COALESCE(pl.desconto,0) AS valor,
                   (CURRENT_DATE - pl.data_fim) AS dias_atraso,
                   (v.placa || ' — ' || v.marca || ' ' || v.modelo) AS descricao
            FROM pagamentos_locacao pl
            JOIN locacoes l ON l.id = pl.locacao_id
            JOIN veiculos v ON v.id = l.veiculo_id
            JOIN clientes c ON c.id = l.cliente_id
            WHERE pl.status = 'pendente' AND pl.data_fim < CURRENT_DATE
            UNION ALL
            SELECT 'multa', m.id,
                   COALESCE(c.nome,'Sem motorista'), COALESCE(c.telefone,''),
                   m.data_infracao::text,
                   COALESCE(m.valor,0) - COALESCE(m.desconto,0),
                   (CURRENT_DATE - m.data_infracao),
                   COALESCE(m.descricao,'Multa')
            FROM multas m
            LEFT JOIN clientes c ON c.id = m.motorista_id
            WHERE m.status = 'pendente'
            UNION ALL
            SELECT 'avulsa', ca.id,
                   COALESCE(c.nome,'Sem cliente'), COALESCE(c.telefone,''),
                   ca.data_vencimento::text,
                   COALESCE(ca.valor,0) - COALESCE(ca.desconto,0),
                   (CURRENT_DATE - ca.data_vencimento),
                   COALESCE(ca.descricao,'Cobrança avulsa')
            FROM cobrancas_avulsas ca
            LEFT JOIN clientes c ON c.id = ca.cliente_id
            WHERE ca.status = 'pendente'
              AND ca.data_vencimento IS NOT NULL
              AND ca.data_vencimento < CURRENT_DATE
            ORDER BY dias_atraso DESC NULLS LAST
            LIMIT 30
        ''')
        cobrancas_vencidas = rows_to_dict(cur)

        cur.execute('''
            SELECT m.id, v.placa, v.marca, v.modelo,
                   COALESCE(c.nome,'Sem motorista') AS motorista,
                   COALESCE(m.descricao,'Multa') AS descricao,
                   COALESCE(m.valor,0) AS valor,
                   m.data_limite_defesa::text AS data_limite_defesa,
                   (m.data_limite_defesa - CURRENT_DATE) AS dias_restantes
            FROM multas m
            JOIN veiculos v ON v.id = m.veiculo_id
            LEFT JOIN clientes c ON c.id = m.motorista_id
            WHERE m.data_limite_defesa IS NOT NULL
              AND m.data_limite_defesa >= CURRENT_DATE
              AND m.data_limite_defesa <= %s
              AND m.status != 'pago'
            ORDER BY m.data_limite_defesa
        ''', (proximos10,))
        multas_prazo = rows_to_dict(cur)

        cur.execute('''
            SELECT v.id, v.placa, v.marca, v.modelo, COALESCE(v.km_atual,0) AS km_atual,
                   mn.proxima_manutencao_km, mn.tipo,
                   (COALESCE(v.km_atual,0) - mn.proxima_manutencao_km) AS km_atrasado
            FROM veiculos v
            JOIN manutencoes mn ON mn.id = (
                SELECT id FROM manutencoes
                WHERE veiculo_id = v.id AND proxima_manutencao_km IS NOT NULL
                ORDER BY data_manutencao DESC, id DESC LIMIT 1
            )
            WHERE v.status != 'inativo'
              AND mn.proxima_manutencao_km IS NOT NULL
              AND COALESCE(v.km_atual,0) >= mn.proxima_manutencao_km
            ORDER BY km_atrasado DESC
        ''')
        manutencoes_atrasadas = rows_to_dict(cur)

    return jsonify({
        'devolucoes_hoje': devolucoes_hoje,
        'devolucoes_semana': devolucoes_semana,
        'cobrancas_vencidas': cobrancas_vencidas,
        'multas_prazo': multas_prazo,
        'manutencoes_atrasadas': manutencoes_atrasadas
    })


@app.route('/relatorios')
@login_required
def relatorios():
    return render_template('relatorios.html')


@app.route('/usuarios')
@login_required
def usuarios():
    if current_user.nivel != 'admin':
        return redirect(url_for('index'))
    return render_template('usuarios.html')


@app.route('/logo_nova.png')
def serve_logo():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'logo_nova.png')

# ==================== UPLOAD GENÉRICO (FOTOS DE LOCAÇÃO etc.) ====================

@app.route('/api/upload-foto', methods=['POST'])
@login_required
def upload_foto_generica():
    try:
        if 'foto' not in request.files:
            return jsonify({'error': 'Nenhum arquivo enviado'}), 400
        file = request.files['foto']
        if file.filename == '' or not allowed_file(file.filename):
            return jsonify({'error': 'Arquivo inválido'}), 400
        folder = request.form.get('folder', 'siqueirao/misc')
        result = cloudinary.uploader.upload(
            file,
            folder=folder,
            resource_type='image'
        )
        return jsonify({'success': True, 'url': result['secure_url']})
    except Exception as e:
        app.logger.error('Erro em upload_foto_generica: %s', e)
        return jsonify({'error': 'Erro ao processar upload. Tente novamente.'}), 500


# ==================== API UPLOAD FOTOS (CLOUDINARY) ====================

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/api/veiculos/<int:id>/foto', methods=['POST'])
@login_required
def upload_foto_veiculo(id):
    try:
        if 'foto' not in request.files:
            return jsonify({'error': 'Nenhum arquivo enviado'}), 400
        file = request.files['foto']
        if file.filename == '' or not allowed_file(file.filename):
            return jsonify({'error': 'Arquivo inválido'}), 400

        public_id = f'siqueirao/veiculo_{id}_{int(datetime.now().timestamp())}'
        result = cloudinary.uploader.upload(file, public_id=public_id, overwrite=True)
        foto_url = result['secure_url']

        with _db() as (conn, cur):
            cur.execute('UPDATE veiculos SET foto = %s WHERE id = %s', (foto_url, id))

        return jsonify({'success': True, 'foto_url': foto_url})
    except Exception as e:
        app.logger.error('Erro em upload_foto_veiculo: %s', e)
        return jsonify({'error': 'Erro ao fazer upload da foto. Tente novamente.'}), 500


@app.route('/api/veiculos/<int:id>/foto', methods=['DELETE'])
@login_required
def delete_foto_veiculo(id):
    try:
        with _db() as (conn, cur):
            cur.execute('UPDATE veiculos SET foto = NULL WHERE id = %s', (id,))
        return jsonify({'success': True})
    except Exception as e:
        app.logger.error('Erro em delete_foto_veiculo: %s', e)
        return jsonify({'error': 'Erro ao remover foto. Tente novamente.'}), 500

# ==================== API CLIENTES ====================

@app.route('/api/clientes', methods=['GET'])
@login_required
def get_clientes():
    with _db() as (conn, cur):
        cur.execute('SELECT * FROM clientes ORDER BY nome')
        result = rows_to_dict(cur)
    return jsonify(result)


@app.route('/api/clientes', methods=['POST'])
@login_required
def add_cliente():
    data = request.json
    try:
        with _db() as (conn, cur):
            cur.execute('''
                INSERT INTO clientes (nome, cpf, cnh, telefone, email, cep, endereco, bairro, cidade, estado, status, observacoes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (data['nome'], data.get('cpf'), data.get('cnh'), data.get('telefone'),
                  data.get('email'), data.get('cep') or None, data.get('endereco'),
                  data.get('bairro') or None, data.get('cidade'),
                  data.get('estado'), data.get('status', 'ativo'), data.get('observacoes')))
        return jsonify({'success': True})
    except psycopg2.IntegrityError:
        return jsonify({'error': 'CPF já cadastrado!'}), 400
    except Exception as e:
        app.logger.error('Erro em add_cliente: %s', e)
        return jsonify({'error': 'Erro interno. Tente novamente.'}), 500


@app.route('/api/clientes/<int:id>', methods=['PUT'])
@login_required
def update_cliente(id):
    data = request.json
    with _db() as (conn, cur):
        cur.execute('''
            UPDATE clientes SET nome=%s, cpf=%s, cnh=%s, telefone=%s, email=%s,
            cep=%s, endereco=%s, bairro=%s, cidade=%s, estado=%s, status=%s, observacoes=%s
            WHERE id=%s
        ''', (data['nome'], data.get('cpf'), data.get('cnh'), data.get('telefone'),
              data.get('email'), data.get('cep') or None, data.get('endereco'),
              data.get('bairro') or None, data.get('cidade'),
              data.get('estado'), data.get('status'), data.get('observacoes'), id))
    return jsonify({'success': True})


@app.route('/api/clientes/<int:id>', methods=['DELETE'])
@login_required
def delete_cliente(id):
    if current_user.nivel != 'admin':
        return jsonify({'error': 'Acesso negado'}), 403
    try:
        with _db() as (conn, cur):
            cur.execute('DELETE FROM clientes WHERE id=%s', (id,))
        return jsonify({'success': True})
    except Exception as e:
        if 'foreign key' in str(e).lower() or 'violates' in str(e).lower():
            return jsonify({'error': 'Cliente possui locações ou cobranças vinculadas e não pode ser excluído.'}), 409
        return jsonify({'error': 'Erro ao excluir cliente'}), 500

@app.route('/api/clientes/importar', methods=['POST'])
@login_required
def importar_clientes():
    if 'file' not in request.files:
        return jsonify({'error': 'Arquivo não enviado'}), 400
    f = request.files['file']
    fname = f.filename.lower()

    try:
        raw = f.read()
        if fname.endswith('.xls'):
            import xlrd
            wb = xlrd.open_workbook(file_contents=raw)
            ws = wb.sheet_by_index(0)
            rows = [[ws.cell_value(r, c) for c in range(ws.ncols)] for r in range(ws.nrows)]
        elif fname.endswith('.xlsx'):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
            ws = wb.active
            rows = [[cell.value for cell in row] for row in ws.iter_rows()]
        else:
            return jsonify({'error': 'Use .xls ou .xlsx'}), 400
    except Exception as e:
        return jsonify({'error': f'Erro ao ler arquivo: {e}'}), 400

    if len(rows) < 2:
        return jsonify({'error': 'Planilha vazia'}), 400

    import unicodedata
    def _norm(s):
        return unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().lower().strip()

    header = [_norm(h) for h in rows[0]]

    def col(*frags):
        for i, h in enumerate(header):
            if any(f in h for f in frags):
                return i
        return -1

    I_NOME   = col('nome')
    I_CPF    = col('cpf', 'cnpj')
    I_EMAIL  = col('email')
    I_CEL    = col('celular')
    I_FONE   = col('fone', 'telefone')
    I_RUA    = col('rua', 'endereco', 'logradouro')
    I_NUM    = col('numero', 'nro', 'n.')
    I_COMP   = col('complemento')
    I_BAIRRO = col('bairro')
    I_CIDADE = col('cidade')
    I_CEP    = col('cep')
    I_UF     = col('estado', 'uf')

    def get(row, i):
        if i < 0 or i >= len(row): return ''
        v = row[i]
        return str(v).strip() if v is not None else ''

    def _to_str_int(v):
        # xlrd lê células numéricas como float (ex: 82720414.0); converte para inteiro antes
        try:
            return str(int(float(v)))
        except (ValueError, TypeError):
            return str(v).strip()

    def fmt_cep(v):
        d = re.sub(r'\D', '', _to_str_int(v))
        return f'{d[:5]}-{d[5:]}' if len(d) == 8 else (d or None)

    def fmt_cpf(v):
        s = _to_str_int(v)
        d = re.sub(r'\D', '', s)
        return d.zfill(11) if d and len(d) <= 11 else (d or None)

    importados = ignorados = 0
    erros = []

    with _db() as (conn, cur):
        for i, row in enumerate(rows[1:], start=2):
            nome = get(row, I_NOME)
            if not nome:
                continue

            cpf = fmt_cpf(get(row, I_CPF))

            if cpf:
                cur.execute('SELECT id FROM clientes WHERE cpf=%s', (cpf,))
                if cur.fetchone():
                    ignorados += 1
                    continue

            telefone = re.sub(r'\D', '', get(row, I_CEL) or get(row, I_FONE)) or None
            email    = get(row, I_EMAIL) or None
            rua      = get(row, I_RUA)
            num      = get(row, I_NUM)
            comp     = get(row, I_COMP)
            endereco = ', '.join(p for p in [rua, num, comp] if p) or None
            bairro   = get(row, I_BAIRRO) or None
            cidade_r = get(row, I_CIDADE)
            cidade   = cidade_r.split(' - ')[0].strip() if ' - ' in cidade_r else (cidade_r or None)
            cep      = fmt_cep(get(row, I_CEP))
            estado   = get(row, I_UF) or None

            try:
                cur.execute('SAVEPOINT sp_imp')
                cur.execute('''
                    INSERT INTO clientes (nome, cpf, telefone, email, cep, endereco, bairro, cidade, estado, status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ''', (nome, cpf, telefone, email, cep, endereco, bairro, cidade, estado, 'ativo'))
                cur.execute('RELEASE SAVEPOINT sp_imp')
                importados += 1
            except Exception as e:
                cur.execute('ROLLBACK TO SAVEPOINT sp_imp')
                erros.append(f'Linha {i} ({nome}): {str(e)[:100]}')

    return jsonify({'importados': importados, 'ignorados': ignorados, 'erros': erros})

# ==================== COBRANÇAS ====================

@app.route('/cobrancas')
@login_required
def cobrancas_page():
    return render_template('cobrancas.html')

@app.route('/api/cobrancas', methods=['GET'])
@login_required
def get_cobrancas():
    with _db() as (conn, cur):
        cur.execute('''
            SELECT l.id, l.data_inicio, l.data_fim, l.diaria,
                   v.placa, v.marca, v.modelo,
                   c.nome AS cliente_nome, c.cpf AS cliente_cpf,
                   c.telefone AS cliente_telefone, c.email AS cliente_email
            FROM locacoes l
            JOIN veiculos v ON v.id = l.veiculo_id
            JOIN clientes c ON c.id = l.cliente_id
            WHERE l.status = 'ativa'
            ORDER BY v.placa, l.data_inicio
        ''')
        locacoes_db = rows_to_dict(cur)

        ids = [l['id'] for l in locacoes_db]
        pagamentos_por_loc = {}
        if ids:
            ph = ','.join(['%s'] * len(ids))
            cur.execute(f'''
                SELECT id, locacao_id, semana_numero, data_inicio, data_fim,
                       valor_previsto, valor_pago, desconto,
                       justificativa_desconto, status, data_pagamento, observacoes,
                       asaas_id, asaas_link, asaas_status, parcela_num, total_parcelas
                FROM pagamentos_locacao
                WHERE locacao_id IN ({ph})
                ORDER BY locacao_id, data_inicio
            ''', ids)
            for p in rows_to_dict(cur):
                lid = p['locacao_id']
                pagamentos_por_loc.setdefault(lid, []).append({
                    'id':                   p['id'],
                    'semana_numero':        p['semana_numero'],
                    'data_inicio':          str(p['data_inicio']) if p['data_inicio'] else None,
                    'data_fim':             str(p['data_fim']) if p['data_fim'] else None,
                    'dias':                 (((_date.fromisoformat(str(p['data_fim'])) - _date.fromisoformat(str(p['data_inicio']))).days)
                                             if p['data_inicio'] and p['data_fim'] else 0),
                    'valor_previsto':       float(p['valor_previsto'] or 0),
                    'valor_pago':           float(p['valor_pago'] or 0),
                    'desconto':             float(p['desconto'] or 0),
                    'justificativa_desconto': p['justificativa_desconto'],
                    'status':               p['status'],
                    'data_pagamento':       str(p['data_pagamento']) if p['data_pagamento'] else None,
                    'observacoes':          p['observacoes'],
                    'asaas_id':             p.get('asaas_id'),
                    'asaas_link':           p.get('asaas_link'),
                    'asaas_status':         p.get('asaas_status'),
                    'parcela_num':          p.get('parcela_num') or 1,
                    'total_parcelas':       p.get('total_parcelas') or 1,
                })

    result = []
    for loc in locacoes_db:
        try:
            di = _date.fromisoformat(str(loc['data_inicio']))
        except Exception:
            continue
        diaria = float(loc['diaria'] or 0)
        pagamentos = pagamentos_por_loc.get(loc['id'], [])
        total_pago = sum(p['valor_pago'] for p in pagamentos)

        result.append({
            'locacao_id':        loc['id'],
            'placa':             loc['placa'],
            'veiculo':           f"{loc['placa']} — {loc['marca']} {loc['modelo']}",
            'cliente':           loc['cliente_nome'],
            'cliente_cpf':       loc.get('cliente_cpf', ''),
            'cliente_telefone':  loc.get('cliente_telefone', ''),
            'cliente_email':     loc.get('cliente_email', ''),
            'data_inicio':       str(loc['data_inicio']),
            'data_fim':          str(loc['data_fim']) if loc['data_fim'] else None,
            'diaria':            diaria,
            'valor_semanal':     round(diaria * 7),
            'pagamentos':        pagamentos,
            'total_pago':        total_pago,
            'n_pagamentos':      len(pagamentos),
        })

    return jsonify(result)

@app.route('/api/cobrancas', methods=['POST'])
@login_required
def registrar_pagamento():
    data       = request.json
    locacao_id = int(data.get('locacao_id'))
    d_ini      = data.get('data_inicio')
    d_fim      = data.get('data_fim')
    desconto   = float(data.get('desconto') or 0)
    just       = (data.get('justificativa_desconto') or '').strip()
    data_pag   = data.get('data_pagamento') or str(_date.today())
    obs        = data.get('observacoes') or None

    status = data.get('status', 'pago')

    if not d_ini or not d_fim:
        return jsonify({'error': 'Informe data de início e fim do período'}), 400
    if d_fim < d_ini:
        return jsonify({'error': 'Data fim deve ser igual ou posterior à data início'}), 400
    if desconto < 0:
        return jsonify({'error': 'Desconto não pode ser negativo'}), 400
    if desconto > 0 and not just:
        return jsonify({'error': 'Justificativa obrigatória quando há desconto'}), 400

    with _db() as (conn, cur):
        # Verifica sobreposição com períodos já registrados
        cur.execute('''
            SELECT id, data_inicio, data_fim FROM pagamentos_locacao
            WHERE locacao_id = %s AND status IN ('pago','pendente')
              AND data_inicio <= %s AND data_fim >= %s
        ''', (locacao_id, d_fim, d_ini))
        overlap = cur.fetchone()
        if overlap:
            return jsonify({'error': f'Período sobrepõe registro já existente ({overlap[1]} a {overlap[2]})'}), 400

        dias = (_date.fromisoformat(d_fim) - _date.fromisoformat(d_ini)).days
        cur.execute('SELECT diaria FROM locacoes WHERE id=%s', (locacao_id,))
        row = cur.fetchone()
        diaria = float(row[0]) if row else 0
        valor_prev = round(diaria * dias)
        valor_pago = round(valor_prev - desconto) if status == 'pago' else 0
        data_pag   = data.get('data_pagamento') or (str(_date.today()) if status == 'pago' else None)

        cur.execute('SELECT COALESCE(MAX(semana_numero),0)+1 FROM pagamentos_locacao WHERE locacao_id=%s', (locacao_id,))
        semana_num = cur.fetchone()[0]

        cur.execute('''
            INSERT INTO pagamentos_locacao
                (locacao_id, semana_numero, data_inicio, data_fim,
                 valor_previsto, valor_pago, desconto, justificativa_desconto,
                 status, data_pagamento, observacoes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        ''', (locacao_id, semana_num, d_ini, d_fim,
              valor_prev, valor_pago, desconto, just or None,
              status, data_pag, obs))
        new_id = cur.fetchone()[0]
    return jsonify({'success': True, 'id': new_id, 'valor_pago': valor_pago, 'valor_previsto': valor_prev, 'dias': dias})

@app.route('/api/cobrar-asaas', methods=['POST'])
@login_required
def cobrar_asaas():
    data       = request.json
    locacao_id = int(data.get('locacao_id'))
    d_ini      = data.get('data_inicio')
    d_fim      = data.get('data_fim')
    desconto   = float(data.get('desconto') or 0)
    billing    = data.get('billing_type', 'PIX')
    parcelas   = data.get('parcelas')   # lista [{data_vencimento, valor}, ...] ou None

    if not d_ini or not d_fim:
        return jsonify({'error': 'Informe data de início e fim do período'}), 400
    if d_fim < d_ini:
        return jsonify({'error': 'Data fim deve ser igual ou posterior à data início'}), 400

    with _db() as (conn, cur):
        cur.execute('''
            SELECT l.diaria, c.nome, c.cpf, c.telefone, c.email, v.placa, v.marca, v.modelo
            FROM locacoes l
            JOIN clientes c ON c.id = l.cliente_id
            JOIN veiculos v ON v.id = l.veiculo_id
            WHERE l.id = %s
        ''', (locacao_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Locação não encontrada'}), 404

        diaria, cli_nome, cli_cpf, cli_tel, cli_email, placa, marca, modelo = row
        diaria = float(diaria or 0)

        # Verifica sobreposição (ignorando registros com mesmo período — são parcelas)
        cur.execute('''
            SELECT id, data_inicio, data_fim FROM pagamentos_locacao
            WHERE locacao_id = %s AND status IN ('pago','pendente')
              AND data_inicio <= %s AND data_fim >= %s
              AND NOT (data_inicio = %s AND data_fim = %s)
        ''', (locacao_id, d_fim, d_ini, d_ini, d_fim))
        overlap = cur.fetchone()
        if overlap:
            return jsonify({'error': f'Período sobrepõe cobrança já registrada ({overlap[1]} a {overlap[2]})'}), 400

        dias       = (_date.fromisoformat(d_fim) - _date.fromisoformat(d_ini)).days + 1
        valor_prev = round(diaria * dias, 2)
        valor_total = round(valor_prev - max(0, desconto), 2)
        desc_base  = f'Locação {placa} {marca} {modelo} — {dias} dia(s) ({d_ini} a {d_fim})'

        customer_id, err = _asaas_get_or_create_customer(cli_cpf, cli_nome, cli_email or '', cli_tel or '')
        if err:
            return jsonify({'error': f'Asaas: {err}'}), 500

        cur.execute('SELECT COALESCE(MAX(semana_numero),0)+1 FROM pagamentos_locacao WHERE locacao_id=%s', (locacao_id,))
        next_seq = cur.fetchone()[0]

        # Monta lista de cobranças a criar
        if parcelas and len(parcelas) > 1:
            n = len(parcelas)
            itens = []
            for i, p in enumerate(parcelas):
                itens.append({
                    'due_date':    p.get('data_vencimento') or str(_date.today()),
                    'valor':       round(float(p.get('valor') or 0), 2),
                    'parcela_num': i + 1,
                    'total':       n,
                    'descricao':   f'{desc_base} — Parcela {i+1}/{n}',
                })
        else:
            due_date = (parcelas[0].get('data_vencimento') if parcelas else None) or data.get('data_vencimento') or str(_date.today())
            itens = [{'due_date': due_date, 'valor': valor_total,
                      'parcela_num': 1, 'total': 1, 'descricao': desc_base}]

        resultados = []
        for idx, item in enumerate(itens):
            charge, sc = _asaas_req('POST', '/payments', {
                'customer':    customer_id,
                'billingType': billing,
                'dueDate':     item['due_date'],
                'value':       item['valor'],
                'description': item['descricao'],
            })
            if sc not in (200, 201):
                errs = charge.get('errors', [{}])
                raise Exception(f"Parcela {item['parcela_num']}: {errs[0].get('description','Erro Asaas')}")

            asaas_id   = charge.get('id')
            asaas_link = charge.get('invoiceUrl', '')
            seq        = next_seq + idx

            cur.execute('''
                INSERT INTO pagamentos_locacao
                    (locacao_id, semana_numero, data_inicio, data_fim,
                     valor_previsto, valor_pago, desconto, status,
                     asaas_id, asaas_link, asaas_status,
                     parcela_num, total_parcelas)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'pendente',%s,%s,'PENDING',%s,%s)
                RETURNING id
            ''', (locacao_id, seq, d_ini, d_fim,
                  round(valor_prev / len(itens), 2), item['valor'],
                  round(max(0, desconto) / len(itens), 2),
                  asaas_id, asaas_link,
                  item['parcela_num'], item['total']))
            new_id = cur.fetchone()[0]

            r = {'id': new_id, 'asaas_id': asaas_id, 'invoice_url': asaas_link,
                 'bank_slip_url': charge.get('bankSlipUrl', ''),
                 'parcela_num': item['parcela_num'], 'total': item['total'],
                 'valor': item['valor']}
            if billing == 'PIX':
                pix, _ = _asaas_req('GET', f'/payments/{asaas_id}/pixQrCode')
                r['pix_qrcode']  = pix.get('encodedImage', '')
                r['pix_payload'] = pix.get('payload', '')
            resultados.append(r)

    return jsonify({'success': True, 'parcelas': resultados})


@app.route('/api/cobrar-asaas-multa', methods=['POST'])
@login_required
def cobrar_asaas_multa():
    data       = request.json
    multa_id   = int(data.get('multa_id'))
    billing    = data.get('billing_type', 'PIX')
    vencimento = data.get('data_vencimento')

    with _db() as (conn, cur):
        cur.execute('''
            SELECT m.valor, m.descricao, m.numero_auto, m.tipo_notificacao,
                   c.nome, c.cpf, c.telefone, c.email
            FROM multas m
            LEFT JOIN clientes c ON m.motorista_id = c.id
            WHERE m.id = %s
        ''', (multa_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Multa não encontrada'}), 404

        valor, desc, num_auto, tipo, cli_nome, cli_cpf, cli_tel, cli_email = row
        valor = float(valor or 0)
        if valor <= 0:
            return jsonify({'error': 'Multa sem valor definido'}), 400
        if not cli_cpf:
            return jsonify({'error': 'Motorista sem CPF cadastrado — não é possível gerar cobrança ASAAS'}), 400

        tipo_label = 'NIC' if tipo == 'nic' else 'Multa'
        descricao  = f'{tipo_label} {num_auto or ""} — {desc or ""}'.strip(' —')

        customer_id, err = _asaas_get_or_create_customer(cli_cpf, cli_nome or '', cli_email or '', cli_tel or '')
        if err:
            return jsonify({'error': err}), 502

        due_date = vencimento or str(_date.today())
        charge, sc = _asaas_req('POST', '/payments', {
            'customer':    customer_id,
            'billingType': billing,
            'value':       valor,
            'dueDate':     due_date,
            'description': descricao,
        })
        if sc not in (200, 201):
            return jsonify({'error': charge.get('errors', [{}])[0].get('description', 'Erro ASAAS')}), 502

        asaas_id   = charge.get('id')
        asaas_link = charge.get('invoiceUrl', '')
        cur.execute('''UPDATE multas SET asaas_id=%s, asaas_link=%s, asaas_status='PENDING' WHERE id=%s''',
                    (asaas_id, asaas_link, multa_id))

    r = {'id': multa_id, 'asaas_id': asaas_id, 'invoice_url': asaas_link, 'asaas_status': 'PENDING'}
    if billing == 'PIX':
        pix, _ = _asaas_req('GET', f'/payments/{asaas_id}/pixQrCode')
        r['pix_code']  = pix.get('payload', '')
        r['pix_image'] = pix.get('encodedImage', '')

    return jsonify(r)


@app.route('/api/cobrar-asaas-multa/<int:multa_id>/verificar', methods=['PUT'])
@login_required
def verificar_asaas_multa(multa_id):
    with _db() as (conn, cur):
        cur.execute('SELECT asaas_id FROM multas WHERE id=%s', (multa_id,))
        row = cur.fetchone()
        if not row or not row[0]:
            return jsonify({'error': 'Sem cobrança ASAAS para esta multa'}), 404

        charge, sc = _asaas_req('GET', f'/payments/{row[0]}')
        if sc != 200:
            return jsonify({'error': 'Cobrança não encontrada no ASAAS'}), 404

        st = charge.get('status', '')
        if st in ('RECEIVED', 'CONFIRMED'):
            cur.execute("UPDATE multas SET status='pago', asaas_status=%s, data_pagamento=%s WHERE id=%s",
                        (st, _date.today(), multa_id))
            return jsonify({'status': 'pago', 'asaas_status': st, 'label': 'Pago', 'cor': '#16a34a'})

        label_map = {'PENDING': ('Aguardando', '#f59e0b'), 'OVERDUE': ('Vencido', '#dc2626'),
                     'CANCELLED': ('Cancelado', '#6b7280')}
        lbl, cor = label_map.get(st, (st, '#64748b'))
        cur.execute('UPDATE multas SET asaas_status=%s WHERE id=%s', (st, multa_id))
    return jsonify({'status': 'pendente', 'asaas_status': st, 'label': lbl, 'cor': cor})


@app.route('/api/cobrancas/<int:pagamento_id>/verificar', methods=['PUT'])
@login_required
def verificar_pagamento(pagamento_id):
    with _db() as (conn, cur):
        cur.execute('SELECT asaas_id FROM pagamentos_locacao WHERE id=%s', (pagamento_id,))
        row = cur.fetchone()
        if not row or not row[0]:
            return jsonify({'error': 'Cobrança Asaas não encontrada para este pagamento'}), 404

        asaas_id = row[0]
        charge, status_code = _asaas_req('GET', f'/payments/{asaas_id}')
        if status_code != 200:
            return jsonify({'error': 'Cobrança não encontrada no Asaas'}), 404

        st = charge.get('status', '')
        if st in ('RECEIVED', 'CONFIRMED'):
            cur.execute('''
                UPDATE pagamentos_locacao
                   SET status='pago', asaas_status=%s, data_pagamento=%s
                 WHERE id=%s
            ''', (st, _date.today(), pagamento_id))
            return jsonify({'status': 'pago', 'asaas_status': st, 'label': 'Pago', 'cor': '#16a34a'})

        label_map = {'PENDING': ('Aguardando', '#f59e0b'), 'OVERDUE': ('Vencido', '#dc2626'),
                     'CANCELLED': ('Cancelado', '#6b7280')}
        lbl, cor = label_map.get(st, (st, '#64748b'))
        cur.execute('UPDATE pagamentos_locacao SET asaas_status=%s WHERE id=%s', (st, pagamento_id))
    return jsonify({'status': 'pendente', 'asaas_status': st, 'label': lbl, 'cor': cor})


@app.route('/api/cobrancas/<int:pagamento_id>', methods=['DELETE'])
@login_required
def cancelar_pagamento(pagamento_id):
    with _db() as (conn, cur):
        cur.execute("UPDATE pagamentos_locacao SET status='cancelado' WHERE id=%s", (pagamento_id,))
    return jsonify({'success': True})

# ==================== API VEÍCULOS ====================

@app.route('/api/veiculos', methods=['GET'])
@login_required
def get_veiculos():
    with _db() as (conn, cur):
        cur.execute('SELECT * FROM veiculos ORDER BY marca, modelo')
        result = rows_to_dict(cur)
    return jsonify(result)


@app.route('/api/veiculos', methods=['POST'])
@login_required
def add_veiculo():
    data = request.json
    try:
        with _db() as (conn, cur):
            cur.execute('''
                INSERT INTO veiculos (placa, marca, modelo, ano, cor, categoria, diaria, km_atual, status, combustivel, observacoes, renavam, chassi, ano_fabricacao, potencia, versao, valor_fipe, codigo_fipe, referencia_fipe, valor_compra)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            ''', (data.get('placa'), data.get('marca'), data.get('modelo'),
                  data.get('ano') or None, data.get('cor') or None,
                  data.get('categoria') or None, data.get('diaria') or None,
                  data.get('km_atual') or 0, data.get('status', 'disponivel'),
                  data.get('combustivel') or None, data.get('observacoes') or None,
                  data.get('renavam') or None, data.get('chassi') or None,
                  data.get('ano_fabricacao') or None,
                  data.get('potencia') or None, data.get('versao') or None,
                  _float_or_none(data.get('valor_fipe')),
                  data.get('codigo_fipe') or None, data.get('referencia_fipe') or None,
                  _float_or_none(data.get('valor_compra'))))
            veiculo_id = cur.fetchone()[0]
        return jsonify({'success': True, 'id': veiculo_id})
    except psycopg2.IntegrityError:
        return jsonify({'error': 'Placa já cadastrada!'}), 400
    except Exception as e:
        app.logger.error('Erro em add_veiculo: %s', e)
        return jsonify({'error': 'Erro interno. Tente novamente.'}), 500


@app.route('/api/veiculos/<int:id>', methods=['PUT'])
@login_required
def update_veiculo(id):
    data = request.json
    try:
        with _db() as (conn, cur):
            cur.execute('''
                UPDATE veiculos SET placa=%s, marca=%s, modelo=%s, ano=%s, cor=%s, categoria=%s,
                diaria=%s, km_atual=%s, status=%s, combustivel=%s, observacoes=%s, renavam=%s,
                chassi=%s, ano_fabricacao=%s, potencia=%s, versao=%s,
                valor_fipe=%s, codigo_fipe=%s, referencia_fipe=%s, valor_compra=%s WHERE id=%s
            ''', (data.get('placa'), data.get('marca'), data.get('modelo'),
                  data.get('ano') or None, data.get('cor'), data.get('categoria'),
                  data.get('diaria') or None, data.get('km_atual') or 0,
                  data.get('status', 'disponivel'), data.get('combustivel'),
                  data.get('observacoes'), data.get('renavam') or None,
                  data.get('chassi') or None,
                  data.get('ano_fabricacao') or None,
                  data.get('potencia') or None, data.get('versao') or None,
                  _float_or_none(data.get('valor_fipe')),
                  data.get('codigo_fipe') or None, data.get('referencia_fipe') or None,
                  _float_or_none(data.get('valor_compra')), id))
        return jsonify({'success': True})
    except psycopg2.IntegrityError:
        return jsonify({'error': 'Placa já cadastrada por outro veículo!'}), 400
    except Exception as e:
        app.logger.error('Erro em update_veiculo: %s', e)
        return jsonify({'error': 'Erro interno. Tente novamente.'}), 500


@app.route('/api/veiculos/<int:id>', methods=['DELETE'])
@login_required
def delete_veiculo(id):
    if current_user.nivel != 'admin':
        return jsonify({'error': 'Acesso negado'}), 403
    try:
        with _db() as (conn, cur):
            cur.execute('DELETE FROM veiculos WHERE id=%s', (id,))
        return jsonify({'success': True})
    except Exception as e:
        if 'foreign key' in str(e).lower() or 'violates' in str(e).lower():
            return jsonify({'error': 'Veículo possui locações ou manutenções vinculadas e não pode ser excluído.'}), 409
        return jsonify({'error': 'Erro ao excluir veículo'}), 500

@app.route('/api/veiculos/<int:id>/crlv', methods=['POST'])
@login_required
def upload_crlv_veiculo(id):
    try:
        if 'crlv' not in request.files:
            return jsonify({'error': 'Nenhum arquivo enviado'}), 400
        file = request.files['crlv']
        if not file.filename.lower().endswith('.pdf'):
            return jsonify({'error': 'Arquivo deve ser PDF'}), 400
        public_id = f'siqueirao/crlv_{id}_{int(datetime.now().timestamp())}'
        result = cloudinary.uploader.upload(
            file, public_id=public_id, resource_type='raw', overwrite=True
        )
        crlv_url = result['secure_url']
        with _db() as (conn, cur):
            cur.execute('UPDATE veiculos SET crlv_url = %s WHERE id = %s', (crlv_url, id))
        return jsonify({'success': True, 'crlv_url': crlv_url})
    except Exception as e:
        app.logger.error('Erro em upload_crlv_veiculo: %s', e)
        return jsonify({'error': 'Erro ao fazer upload do CRLV. Tente novamente.'}), 500


@app.route('/api/veiculos/<int:id>/crlv/download')
@login_required
def download_crlv_veiculo(id):
    try:
        with _db() as (conn, cur):
            cur.execute('SELECT crlv_url, placa FROM veiculos WHERE id = %s', (id,))
            row = cur.fetchone()
        if not row or not row[0]:
            return jsonify({'error': 'CRLV não encontrado'}), 404
        crlv_url, placa = row[0], row[1]
        import requests as req_http
        r = req_http.get(crlv_url, timeout=30)
        return Response(
            r.content,
            mimetype='application/pdf',
            headers={
                'Content-Disposition': f'attachment; filename="CRLV_{placa}.pdf"',
                'Content-Type': 'application/pdf',
            }
        )
    except Exception as e:
        app.logger.error('Erro em download_crlv_veiculo: %s', e)
        return jsonify({'error': 'Erro ao baixar CRLV. Tente novamente.'}), 500


@app.route('/api/veiculos/<int:id>/crlv', methods=['DELETE'])
@login_required
def delete_crlv_veiculo(id):
    try:
        with _db() as (conn, cur):
            cur.execute('UPDATE veiculos SET crlv_url = NULL WHERE id = %s', (id,))
        return jsonify({'success': True})
    except Exception as e:
        app.logger.error('Erro em delete_crlv_veiculo: %s', e)
        return jsonify({'error': 'Erro ao remover CRLV. Tente novamente.'}), 500


# ==================== IMPORTAR CRLV ====================

@app.route('/api/importar-crlv', methods=['POST'])
@login_required
def importar_crlv():
    if 'crlv' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado'}), 400
    arquivo = request.files['crlv']
    if not arquivo.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Arquivo deve ser PDF'}), 400
    try:
        pdf_bytes = arquivo.read()
        texto = ''
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    texto += t + '\n'

        dados = _parse_crlv(texto)
        if not dados:
            return jsonify({'error': 'Não foi possível extrair dados do CRLV. Verifique se o PDF é válido.'}), 422
        return jsonify({'success': True, 'dados': dados, 'texto_bruto': texto[:2000]})
    except Exception as e:
        app.logger.error('Erro em importar_crlv: %s', e)
        return jsonify({'error': 'Erro ao processar PDF. Verifique se o arquivo é válido.'}), 500


def _parse_crlv(texto):
    dados = {}

    linhas = [re.sub(r'[ \t]+', ' ', l).strip() for l in texto.split('\n')]
    linhas = [l for l in linhas if l]

    def proxima_valor(pattern):
        for i, l in enumerate(linhas):
            if re.search(pattern, l, re.IGNORECASE):
                if i + 1 < len(linhas):
                    return linhas[i + 1]
        return None

    def idx_label(pattern):
        for i, l in enumerate(linhas):
            if re.search(pattern, l, re.IGNORECASE):
                return i
        return None

    # ---------- RENAVAM ----------
    m = re.search(r'\b(\d{11})\b', texto)
    if m:
        dados['renavam'] = m.group(1)

    # ---------- PLACA ----------
    m = re.search(r'\b([A-Z]{3}\d[A-Z]\d{2})\b', texto)   # Mercosul
    if not m:
        m = re.search(r'\b([A-Z]{3}\d{4})\b', texto)       # antigo
    if m:
        dados['placa'] = m.group(1)

    # ---------- CHASSI ----------
    m = re.search(r'\b([A-HJ-NPR-Z0-9]{17})\b', texto)
    if m:
        dados['chassi'] = m.group(1)

    # ---------- ANOS ----------
    # 1ª tentativa: linha seguinte ao label com "XXXX/XXXX" ou "XXXX XXXX"
    i_ano = idx_label(r'ANO\s+(DE\s+)?FABRIC|ANO\s+FAB')
    if i_ano is not None:
        for l in linhas[i_ano + 1: i_ano + 4]:
            # Aceita barra ou espaço como separador entre os dois anos
            m = re.search(r'(19\d{2}|20[012]\d)\s*[/\s]\s*(19\d{2}|20[012]\d)', l)
            if m:
                dados['ano_fabricacao'] = int(m.group(1))
                dados['ano'] = int(m.group(2))
                break
            # Um único ano na linha
            m = re.search(r'\b(19\d{2}|20[012]\d)\b', l)
            if m:
                dados['ano_fabricacao'] = int(m.group(1))
                break
    # 2ª tentativa: padrão "XXXX/XXXX" em qualquer linha do texto
    if 'ano_fabricacao' not in dados:
        for l in linhas:
            m = re.search(r'\b(19\d{2}|20[012]\d)\s*/\s*(19\d{2}|20[012]\d)\b', l)
            if m:
                dados['ano_fabricacao'] = int(m.group(1))
                dados['ano'] = int(m.group(2))
                break
    # 3ª tentativa: se achou só fab, busca modelo separado
    if 'ano_fabricacao' in dados and 'ano' not in dados:
        val_mod = proxima_valor(r'ANO\s+MODELO|ANO\s+MOD')
        if val_mod:
            m = re.search(r'\b(19\d{2}|20[012]\d)\b', val_mod)
            if m:
                dados['ano'] = int(m.group(1))

    # ---------- MARCA / MODELO / VERSÃO ----------
    # O pdfplumber mescla colunas do CRLV, gerando ruído após o valor real.
    # Além disso, o DENATRAN usa prefixo "I/" (importado) ou "N/" (nacional)
    # antes da marca real: "I/CHEVROLET CLASSIC LS DADOS DO SEGURO DPVAT *"
    MARCAS_ABREV = {'VW', 'GM', 'BMW', 'KIA', 'JAC', 'BYD', 'GWM', 'MG', 'GAC', 'JMC'}
    # Códigos DENATRAN de origem (1–2 letras) que precedem a marca real
    CODIGOS_ORIGEM = {'I', 'N', 'E', 'C', 'M'}

    val_mmv = proxima_valor(r'MARCA\s*/?\s*MODELO')
    if val_mmv:
        # 1. Remove ruído de coluna direita: trunca em espaço duplo ou em palavras-chave
        #    típicas da coluna DPVAT/SEGURO que pdfplumber mescla na mesma linha
        val_mmv = re.split(
            r'\s{2,}|\s+(?=DADOS\s+DO\s+SEGURO|REPASSE\s+OBRIG|CAT[.\s]+TARIF'
            r'|COTA\s+[UÚ]NICA|INFORMA[ÇC][ÕO]ES\s+DO\s+SEGURO)',
            val_mmv, flags=re.IGNORECASE
        )[0].strip()
        # 2. Remove asteriscos e qualquer coisa após eles (campos mascarados do CRLV)
        val_mmv = re.sub(r'\s*\*+.*$', '', val_mmv).strip()

        if '/' in val_mmv:
            prefixo, _, resto_text = val_mmv.partition('/')
            prefixo = prefixo.strip().upper()
            tokens = resto_text.strip().split()

            # Prefixo curto = código de origem (I, N…), a marca real é a 1ª palavra do resto
            if prefixo in CODIGOS_ORIGEM or (len(prefixo) <= 2 and prefixo.isalpha()):
                if tokens:
                    marca_raw = tokens[0].upper()
                    dados['marca'] = marca_raw if marca_raw in MARCAS_ABREV else marca_raw.title()
                    tokens = tokens[1:]   # o resto é MODELO [VERSÃO]
            else:
                # Prefixo já é a marca (VW, FIAT, CHEVROLET, HONDA…)
                dados['marca'] = prefixo if prefixo in MARCAS_ABREV else prefixo.title()
                # tokens já é MODELO [VERSÃO]

            # Detecta token de versão numérica: "1.0", "1.4T", "1.6i", "2.0T"
            versao_token = next(
                (t for t in tokens if re.match(r'^\d+[.,]\d+\w{0,3}$', t)),
                None
            )
            if versao_token:
                vi = tokens.index(versao_token)
                dados['modelo'] = ' '.join(tokens[:vi]).title() if vi > 0 else ''
                dados['versao'] = versao_token
            else:
                # Sem versão numérica: tudo é o modelo (ex: "Classic Ls", "Gol G7")
                dados['modelo'] = ' '.join(tokens).title()
        elif val_mmv:
            dados['modelo'] = val_mmv.title()

    # ---------- COR PREDOMINANTE ----------
    # O layout de colunas do CRLV faz o pdfplumber pular a cor e pegar outro texto.
    # Estratégia: procura nome de cor brasileiro em até 8 linhas após o label.
    CORES = ['PRETA','PRET','BRANCA','BRANCO','VERMELHA','VERMELHO','PRATA',
             'CINZA','AZUL','VERDE','AMARELA','AMARELO','MARROM','BEGE',
             'LARANJA','ROXA','ROXO','DOURADA','DOURADO','CHAMPANHE','GRAFITE',
             'VINHO','BORDO','BORDÔ','CREME','ROSA','BRONZE','PRETO']
    i_cor = idx_label(r'COR\s+PREDOMINANTE')
    cor_encontrada = None
    if i_cor is not None:
        for l in linhas[i_cor + 1: i_cor + 9]:
            for c in CORES:
                if re.search(r'\b' + c + r'\b', l, re.IGNORECASE):
                    cor_encontrada = 'Preta' if c.upper() in ('PRET', 'PRETO') else c.title()
                    break
            if cor_encontrada:
                break
    if cor_encontrada:
        dados['cor'] = cor_encontrada

    # ---------- COMBUSTÍVEL ----------
    # Mesma estratégia: busca pelo nome do combustível em até 8 linhas após o label.
    MAPA_COMB = {
        'GASOLINA':'Gasolina','ALCOOL':'Álcool','ÁLCOOL':'Álcool',
        'ETANOL':'Álcool','FLEX':'Flex','DIESEL':'Diesel',
        'GNV':'GNV','ELETRICO':'Elétrico','ELÉTRICO':'Elétrico',
        'HIBRIDO':'Híbrido','HÍBRIDO':'Híbrido',
    }
    i_comb = idx_label(r'COMBUST[IÍ]VEL')
    if i_comb is not None:
        for l in linhas[i_comb + 1: i_comb + 8]:
            # FLEX: GASOLINA e ÁLCOOL/ETANOL na mesma linha ("ÁLCOOL/GASOLINA", "GASOLINA E ÁLCOOL")
            has_gas = bool(re.search(r'\bGASOLINA\b', l, re.IGNORECASE))
            has_alc = bool(re.search(r'\b[AÁ]LCOOL\b|\bETANOL\b', l, re.IGNORECASE))
            if has_gas and has_alc:
                dados['combustivel'] = 'Flex'
                break
            # Combustível único
            m = re.search(
                r'\b(GASOLINA|[AÁ]LCOOL|ETANOL|FLEX|DIESEL|GNV|EL[EÉ]TRICO|H[IÍ]BRIDO)\b',
                l, re.IGNORECASE
            )
            if m:
                dados['combustivel'] = MAPA_COMB.get(m.group(1).upper(), m.group(1).title())
                break

    # ---------- POTÊNCIA ----------
    m = re.search(r'(\d+\s*[Cc][Vv][/\s]\d+)', texto)
    if m:
        dados['potencia'] = m.group(1).strip()

    # ---------- ESPÉCIE / TIPO → CATEGORIA ----------
    # TIPO tem precedência (mais específico). Busca por palavra-chave em todo o texto
    # para não depender da posição exata do label (que varia por estado/formato do CRLV).
    TIPOS_CAT = [
        (r'\bPASSEIO\b',    'Econômico'),
        (r'\bMISTO\b',      'SUV'),
        (r'\bCARGA\b',      'Utilitário'),
        (r'\bCORRIDA\b',    'Executivo'),
    ]
    ESPECIES_CAT = [
        (r'\bCAMINHONETA\b',  'SUV'),
        (r'\bCAMINHONETE\b',  'Utilitário'),
        (r'\bUTILIT[AÁ]RIO\b','Utilitário'),
        (r'\bMICROONIBUS\b',  'Utilitário'),
        (r'\bONIBUS\b',       'Utilitário'),
        (r'\bAUT[OÔ]M[OÓ]VEL\b', 'Econômico'),
        (r'\bAUTOMOVEL\b',    'Econômico'),
    ]

    # 1ª tentativa: próximas 6 linhas após o label ESPÉCIE
    i_esp = idx_label(r'ESP[EÉ]CIE')
    if i_esp is not None:
        trecho = ' '.join(linhas[i_esp + 1: i_esp + 6]).upper()
        for pat, cat in TIPOS_CAT:
            if re.search(pat, trecho):
                dados['categoria'] = cat
                break
        if 'categoria' not in dados:
            for pat, cat in ESPECIES_CAT:
                if re.search(pat, trecho):
                    dados['categoria'] = cat
                    break

    # 2ª tentativa: varredura em todo o texto (ESPÉCIE costuma ser termo único no CRLV)
    if 'categoria' not in dados:
        texto_up = texto.upper()
        for pat, cat in TIPOS_CAT:
            if re.search(pat, texto_up):
                dados['categoria'] = cat
                break
        if 'categoria' not in dados:
            for pat, cat in ESPECIES_CAT:
                if re.search(pat, texto_up):
                    dados['categoria'] = cat
                    break

    return dados


# ==================== API MANUTENÇÕES ====================

@app.route('/api/historico-veiculo/<placa>', methods=['GET'])
@login_required
def historico_veiculo(placa):
    with _db() as (conn, cur):
        cur.execute(
            'SELECT id, placa, marca, modelo, ano, ano_fabricacao, km_atual, status, cor, combustivel, categoria '
            'FROM veiculos WHERE UPPER(placa) = %s',
            (placa.upper(),)
        )
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Veículo não encontrado'}), 404
        cols = [d[0] for d in cur.description]
        veiculo = dict(zip(cols, row))

        cur.execute(
            'SELECT m.*, v.placa, v.marca, v.modelo '
            'FROM manutencoes m LEFT JOIN veiculos v ON m.veiculo_id = v.id '
            'WHERE m.veiculo_id = %s ORDER BY m.data_manutencao DESC, m.id DESC',
            (veiculo['id'],)
        )
        manutencoes_list = rows_to_dict(cur)
    return jsonify({'veiculo': veiculo, 'manutencoes': manutencoes_list})


@app.route('/api/manutencoes', methods=['GET'])
@login_required
def get_manutencoes():
    with _db() as (conn, cur):
        cur.execute('''
            SELECT m.*, v.placa, v.marca, v.modelo
            FROM manutencoes m
            LEFT JOIN veiculos v ON m.veiculo_id = v.id
            ORDER BY m.data_manutencao DESC
        ''')
        result = rows_to_dict(cur)
    return jsonify(result)


@app.route('/api/manutencoes/veiculo/<int:veiculo_id>')
@login_required
def get_manutencoes_veiculo(veiculo_id):
    with _db() as (conn, cur):
        cur.execute('SELECT * FROM manutencoes WHERE veiculo_id = %s ORDER BY data_manutencao DESC', (veiculo_id,))
        result = rows_to_dict(cur)
    return jsonify(result)


@app.route('/api/manutencoes', methods=['POST'])
@login_required
def add_manutencao():
    data = request.json
    with _db() as (conn, cur):
        cur.execute('''
            INSERT INTO manutencoes (veiculo_id, tipo, descricao, data_manutencao, km_manutencao,
            custo, oficina, proxima_manutencao_km, proxima_manutencao_data, status,
            numero_pedido, fornecedor_id, itens_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (_int_or_none(data.get('veiculo_id')),
              data.get('tipo'),
              data.get('descricao') or None,
              _normalize_date(data.get('data_manutencao')),
              _int_or_none(data.get('km_manutencao')),
              _float_or_none(data.get('custo')),
              data.get('oficina') or None,
              _int_or_none(data.get('proxima_manutencao_km')),
              _normalize_date(data.get('proxima_manutencao_data')),
              data.get('status') or 'concluida',
              data.get('numero_pedido') or None,
              _int_or_none(data.get('fornecedor_id')),
              data.get('itens_json') or None))

        vid = _int_or_none(data.get('veiculo_id'))
        if vid:
            cur.execute('''
                UPDATE veiculos SET km_atual = (
                    SELECT km_manutencao FROM manutencoes
                    WHERE veiculo_id = %s AND km_manutencao IS NOT NULL
                    ORDER BY data_manutencao DESC, id DESC LIMIT 1
                )
                WHERE id = %s AND (
                    SELECT km_manutencao FROM manutencoes
                    WHERE veiculo_id = %s AND km_manutencao IS NOT NULL
                    ORDER BY data_manutencao DESC, id DESC LIMIT 1
                ) IS NOT NULL
            ''', (vid, vid, vid))

    return jsonify({'success': True})


def _normalize_date(v):
    if not v or str(v).strip() == '':
        return None
    s = str(v).strip()
    if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        return s
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', s)
    if m:
        return f'{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}'
    return None


def _int_or_none(v):
    if v is None or v == '':
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

def _float_or_none(v):
    if v is None or v == '':
        return None
    try:
        return float(str(v).replace(',', '.'))
    except (TypeError, ValueError):
        return None


@app.route('/api/manutencoes/<int:id>', methods=['PUT'])
@login_required
def update_manutencao(id):
    data = request.json
    with _db() as (conn, cur):
        cur.execute('''
            UPDATE manutencoes SET veiculo_id=%s, tipo=%s, descricao=%s, data_manutencao=%s,
            km_manutencao=%s, custo=%s, oficina=%s, proxima_manutencao_km=%s,
            proxima_manutencao_data=%s, status=%s, fornecedor_id=%s, numero_pedido=%s, itens_json=%s
            WHERE id=%s
        ''', (_int_or_none(data.get('veiculo_id')),
              data.get('tipo'),
              data.get('descricao') or None,
              _normalize_date(data.get('data_manutencao')),
              _int_or_none(data.get('km_manutencao')),
              _float_or_none(data.get('custo')),
              data.get('oficina') or None,
              _int_or_none(data.get('proxima_manutencao_km')),
              _normalize_date(data.get('proxima_manutencao_data')),
              data.get('status'),
              _int_or_none(data.get('fornecedor_id')),
              data.get('numero_pedido') or None,
              data.get('itens_json') or None,
              id))

        vid = _int_or_none(data.get('veiculo_id'))
        if vid:
            cur.execute('''
                UPDATE veiculos SET km_atual = (
                    SELECT km_manutencao FROM manutencoes
                    WHERE veiculo_id = %s AND km_manutencao IS NOT NULL
                    ORDER BY data_manutencao DESC, id DESC LIMIT 1
                )
                WHERE id = %s AND (
                    SELECT km_manutencao FROM manutencoes
                    WHERE veiculo_id = %s AND km_manutencao IS NOT NULL
                    ORDER BY data_manutencao DESC, id DESC LIMIT 1
                ) IS NOT NULL
            ''', (vid, vid, vid))

    return jsonify({'success': True})


@app.route('/api/importar-manutencao-pdf', methods=['POST'])
@login_required
def importar_manutencao_pdf():
    if 'pdf' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado'}), 400
    pdf_file = request.files['pdf']
    if not pdf_file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Arquivo deve ser PDF'}), 400
    try:
        pdf_bytes = io.BytesIO(pdf_file.read())
        all_text = ''
        all_tables = []
        with pdfplumber.open(pdf_bytes) as pdf:
            for page in pdf.pages:
                all_text += (page.extract_text() or '') + '\n'
                tbls = page.extract_tables()
                if tbls:
                    all_tables.extend(tbls)

        result = {}

        # Número do pedido
        m = re.search(r'PEDIDO\s+N[Oº°]\s*(\d+)', all_text, re.IGNORECASE)
        result['numero_pedido'] = m.group(1) if m else ''

        # Nome do fornecedor — primeira linha antes da data/hora
        lines = [l.strip() for l in all_text.split('\n') if l.strip()]
        fornec_nome_raw = ''
        if lines:
            fornec_nome_raw = re.sub(r'\s+\d{1,2}/\d{1,2}/\d{2,4}.*', '', lines[0]).strip()
        result['fornecedor_nome_raw'] = fornec_nome_raw

        # CNPJ do fornecedor
        m = re.search(r'CNPJ[:\s]+([\d./-]+)', all_text, re.IGNORECASE)
        cnpj_raw = m.group(1).strip() if m else ''
        result['fornecedor_cnpj_raw'] = cnpj_raw

        # Data do serviço — múltiplos formatos, busca agressiva
        meses_pt = {'jan':'01','fev':'02','mar':'03','abr':'04','mai':'05','jun':'06',
                    'jul':'07','ago':'08','set':'09','out':'10','nov':'11','dez':'12'}
        data_parsed = ''
        # 1) Com prefixo "Data:" — formato mês por extenso
        m = re.search(r'Data[:\s]+(\d{1,2})/(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)/(\d{4})', all_text, re.IGNORECASE)
        if m:
            data_parsed = f'{m.group(3)}-{meses_pt[m.group(2).lower()]}-{int(m.group(1)):02d}'
        # 2) Qualquer "dd/mmm/yyyy" no texto (sem exigir "Data:")
        if not data_parsed:
            m = re.search(r'(\d{1,2})/(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)/(\d{4})', all_text, re.IGNORECASE)
            if m:
                data_parsed = f'{m.group(3)}-{meses_pt[m.group(2).lower()]}-{int(m.group(1)):02d}'
        # 3) Formato numérico: dd/mm/yyyy com prefixo "Data:"
        if not data_parsed:
            m = re.search(r'Data[:\s]+(\d{2})/(\d{2})/(\d{4})', all_text, re.IGNORECASE)
            if m:
                data_parsed = f'{m.group(3)}-{m.group(2)}-{m.group(1)}'
        # 4) Qualquer dd/mm/yyyy no texto (fallback mais agressivo)
        if not data_parsed:
            m = re.search(r'\b(\d{2})/(\d{2})/(\d{4})\b', all_text)
            if m:
                data_parsed = f'{m.group(3)}-{m.group(2)}-{m.group(1)}'
        result['data_manutencao'] = data_parsed
        if app.debug:
            result['_debug_data_raw'] = re.findall(r'\d{1,2}/\w{2,3}/\d{4}', all_text)[:3]

        # Placa — padrão Mercosul e antigo
        m = re.search(r'Placa[:\s|]+([A-Z]{3}[\d][A-Z\d][\d]{2})', all_text, re.IGNORECASE)
        result['placa'] = m.group(1).upper() if m else ''

        # KM
        m = re.search(r'\bKm[:\s|]+([\d.,]+)', all_text, re.IGNORECASE)
        if m:
            try:
                result['km'] = int(m.group(1).replace('.', '').replace(',', ''))
            except Exception:
                result['km'] = None
        else:
            result['km'] = None

        # ─── ITENS ───────────────────────────────────────────────────────────────────────────
        def _monetario(s):
            s = (s or '').strip().replace('.', '').replace(',', '.')
            try:
                return float(s)
            except Exception:
                return None

        # Localiza bloco de itens no texto
        m_ini_it = re.search(r'ITENS\s+DO\s+PEDIDO|ITENS\s+DO\s+OR|ITENS\s+DA\s+OS', all_text, re.IGNORECASE)
        m_fim_it = re.search(r'Totalizadores|TOTAL\s+GERAL|Total\s+da\s+OS|Subtotal', all_text, re.IGNORECASE)
        bloco_itens = all_text[m_ini_it.end():m_fim_it.start()] if (m_ini_it and m_fim_it) else all_text

        itens = []

        # Estrategia 1 (PRIMARIA): regex especifico para distribuidoras brasileiras
        # Formato: <seq> <cod_num> <desc> <qtd>,XX <unidade_letras> <desc%>,XX <unit>,XX <total>,XX
        re_br = re.compile(
            r'^\s*(\d{1,3})\s+(\d+)\s+'
            r'(.+?)\s+'
            r'(\d+,\d{2})\s+'
            r'([A-Za-z\u00e7\u00c7\u00e3\u00c3\u00e1\u00e9\u00ed\u00f3\u00fa\u00e2\u00ea\u00f4\u00e0\u00f5]{1,6})\s+'
            r'\d+,\d{2}\s+'
            r'(\d+,\d{2})\s+'
            r'(\d+,\d{2})',
            re.MULTILINE | re.IGNORECASE
        )
        for mi in re_br.finditer(bloco_itens):
            try:
                total_v = float(mi.group(7).replace(',', '.'))
                if total_v <= 0:
                    continue
                itens.append({
                    'codigo': mi.group(2),
                    'descricao': mi.group(3).strip(),
                    'quantidade': float(mi.group(4).replace(',', '.')),
                    'unidade': mi.group(5),
                    'valor_unitario': float(mi.group(6).replace(',', '.')),
                    'valor_total': total_v
                })
            except Exception:
                pass

        # Estrategia 2: sem exigir desc% explicito
        if not itens:
            re_gen = re.compile(
                r'^\s*(\d{1,3})\s+(\d+)\s+'
                r'(.+?)\s+'
                r'(\d+,\d{2})\s+'
                r'([A-Za-z\u00e7\u00c7\u00e3\u00c3\u00e1\u00e9\u00ed\u00f3\u00fa]{1,6})\s+'
                r'(?:\d+,\d{2}\s+)?'
                r'(\d+,\d{2})\s+'
                r'(\d+,\d{2})',
                re.MULTILINE | re.IGNORECASE
            )
            for mi in re_gen.finditer(bloco_itens):
                try:
                    total_v = float(mi.group(7).replace(',', '.'))
                    if total_v <= 0:
                        continue
                    itens.append({
                        'codigo': mi.group(2),
                        'descricao': mi.group(3).strip(),
                        'quantidade': float(mi.group(4).replace(',', '.')),
                        'unidade': mi.group(5),
                        'valor_unitario': float(mi.group(6).replace(',', '.')),
                        'valor_total': total_v
                    })
                except Exception:
                    pass

        # Estrategia 3: tabela pdfplumber (fallback)
        if not itens:
            for table in all_tables:
                if not table or len(table) < 2:
                    continue
                h_idx = 0
                for i, row in enumerate(table[:5]):
                    if row and any('DESCRI' in str(c or '').upper() for c in row):
                        h_idx = i
                        break
                header = [str(c or '').upper().strip() for c in table[h_idx]]
                def _ci(keywords, exact=False):
                    for i, h in enumerate(header):
                        if exact:
                            if h in [k.upper() for k in keywords]:
                                return i
                        else:
                            if any(k.upper() in h for k in keywords):
                                return i
                    return -1
                idx_d = _ci(['DESCRI'])
                idx_t = _ci(['TOTAL R', 'TOTAL'])
                idx_u = _ci(['UNIT'])
                idx_q = _ci(['QUANT', 'QTD'])
                idx_n = _ci(['UN'], exact=True)
                if idx_d < 0 or idx_t < 0:
                    continue
                for row in table[h_idx + 1:]:
                    if not row or not row[0]:
                        continue
                    rs = [str(c or '').strip() for c in row]
                    if not re.match(r'^\d{1,3}(\s+\S.*)?$', rs[0]):
                        continue
                    try:
                        desc = rs[idx_d] if idx_d < len(rs) else ''
                        if not desc or re.match(r'^[\d.,]+$', desc):
                            continue
                        total_v = _monetario(rs[idx_t] if idx_t < len(rs) else '') or 0.0
                        if total_v <= 0:
                            continue
                        u_price = _monetario(rs[idx_u] if idx_u >= 0 and idx_u < len(rs) else '') or total_v
                        qty = _monetario(rs[idx_q] if idx_q >= 0 and idx_q < len(rs) else '') or 1.0
                        un = rs[idx_n] if idx_n >= 0 and idx_n < len(rs) and not re.match(r'^[\d.,]+$', rs[idx_n]) else ''
                        cod = rs[1] if len(rs) > 1 and re.match(r'^\d+$', rs[1]) else ''
                        itens.append({'codigo': cod, 'descricao': desc, 'quantidade': qty,
                                      'unidade': un, 'valor_unitario': u_price, 'valor_total': total_v})
                    except Exception:
                        pass
                if itens:
                    break

        # Remove duplicatas mantendo ordem
        vistos = set()
        itens_ok = []
        for it in itens:
            chave = (it['descricao'].lower().strip(), round(it['valor_total'], 2))
            if chave not in vistos and it['valor_total'] > 0:
                vistos.add(chave)
                itens_ok.append(it)
        itens = itens_ok

        result['itens'] = itens
        if app.debug:
            result['_debug_bloco_itens'] = bloco_itens[:500] if bloco_itens else ''
            result['_debug_n_tabelas'] = len(all_tables)
            result['_debug_text_inicio'] = all_text[:300]
            result['_debug_table_headers'] = [
                [str(c or '') for c in (t[0] if t else [])] for t in all_tables[:3]
            ]

        # Total: soma dos itens (confiável) — evita bug do regex capturar nº de itens
        if itens:
            result['total'] = round(sum(i['valor_total'] for i in itens), 2)
        else:
            # Fallback: último valor monetário na seção totalizadores
            m_tot = re.search(r'Totalizadores([\s\S]{0,600})', all_text, re.IGNORECASE)
            if m_tot:
                nums = re.findall(r'(\d{1,3}(?:\.\d{3})*,\d{2})', m_tot.group(1))
                try:
                    result['total'] = float(nums[-1].replace('.', '').replace(',', '.')) if nums else 0.0
                except Exception:
                    result['total'] = 0.0
            else:
                result['total'] = 0.0

        # Cruzamento de veículo e fornecedor
        with _db() as (conn, cur):
            veiculo_match = None
            if result.get('placa'):
                cur.execute(
                    'SELECT id, placa, marca, modelo, ano_fabricacao, km_atual FROM veiculos WHERE UPPER(placa) = %s',
                    (result['placa'].upper(),)
                )
                row = cur.fetchone()
                if row:
                    veiculo_match = {'id': row[0], 'placa': row[1], 'marca': row[2],
                                     'modelo': row[3], 'ano': row[4], 'km_atual': row[5]}
            result['veiculo'] = veiculo_match

            fornecedor_match = None
            cnpj_clean = re.sub(r'\D', '', cnpj_raw)
            if cnpj_clean:
                cur.execute(
                    "SELECT id, nome, nome_fantasia, cnpj FROM fornecedores "
                    "WHERE REGEXP_REPLACE(COALESCE(cnpj,''),'[^0-9]','','g') = %s LIMIT 1",
                    (cnpj_clean,)
                )
                row = cur.fetchone()
                if row:
                    fornecedor_match = {'id': row[0], 'nome': row[1], 'nome_fantasia': row[2], 'cnpj': row[3]}
            if not fornecedor_match and fornec_nome_raw:
                q = fornec_nome_raw.upper()[:25]
                cur.execute(
                    "SELECT id, nome, nome_fantasia, cnpj FROM fornecedores "
                    "WHERE UPPER(nome) LIKE %s OR UPPER(COALESCE(nome_fantasia,'')) LIKE %s LIMIT 1",
                    (f'%{q}%', f'%{q}%')
                )
                row = cur.fetchone()
                if row:
                    fornecedor_match = {'id': row[0], 'nome': row[1], 'nome_fantasia': row[2], 'cnpj': row[3]}
            result['fornecedor'] = fornecedor_match

        return jsonify(result)

    except Exception as e:
        app.logger.error('Erro em importar_manutencao_pdf: %s', e)
        return jsonify({'error': 'Erro ao processar PDF. Verifique se o arquivo é válido.'}), 500


@app.route('/api/manutencoes/<int:id>', methods=['DELETE'])
@login_required
def delete_manutencao(id):
    with _db() as (conn, cur):
        cur.execute('DELETE FROM manutencoes WHERE id=%s', (id,))
    return jsonify({'success': True})

# ==================== API LOCAÇÕES ====================

@app.route('/api/locacoes', methods=['GET'])
@login_required
def get_locacoes():
    with _db() as (conn, cur):
        cur.execute('''
            SELECT l.*, v.placa, v.marca, v.modelo, c.nome as nome_cliente
            FROM locacoes l
            LEFT JOIN veiculos v ON l.veiculo_id = v.id
            LEFT JOIN clientes c ON l.cliente_id = c.id
            ORDER BY l.data_inicio DESC
        ''')
        result = rows_to_dict(cur)
    return jsonify(result)


@app.route('/api/locacoes/<int:id>', methods=['GET'])
@login_required
def get_locacao(id):
    with _db() as (conn, cur):
        cur.execute('''
            SELECT l.*, v.placa, v.marca, v.modelo, v.cor, v.ano, v.combustivel,
                   c.nome as nome_cliente, c.cpf, c.cnh, c.telefone, c.endereco, c.cidade, c.estado
            FROM locacoes l
            LEFT JOIN veiculos v ON l.veiculo_id = v.id
            LEFT JOIN clientes c ON l.cliente_id = c.id
            WHERE l.id = %s
        ''', (id,))
        result = row_to_dict(cur)
    if not result:
        return jsonify({'error': 'Locação não encontrada'}), 404
    return jsonify(result)


def _gerar_periodos_futuros(cur, locacao_id, data_inicio, freq, diaria):
    """Gera pagamentos pendentes do período atual até 31/dez do ano vigente."""
    freq_dias = {'semanal': 7, 'quinzenal': 15, 'mensal': 30}
    n_dias = freq_dias.get(freq)
    if not n_dias:
        return
    hoje = _date.today()
    fim_ano = _date(hoje.year, 12, 31)
    diaria_f = float(diaria or 0)
    if isinstance(data_inicio, str):
        data_inicio = _date.fromisoformat(data_inicio)
    # avança até o primeiro período que ainda não terminou
    periodo_ini = data_inicio
    while periodo_ini + timedelta(days=n_dias - 1) < hoje:
        periodo_ini += timedelta(days=n_dias)
    while periodo_ini <= fim_ano:
        periodo_fim = min(periodo_ini + timedelta(days=n_dias - 1), fim_ano)
        cur.execute('''SELECT COUNT(*) FROM pagamentos_locacao
                       WHERE locacao_id=%s AND data_inicio <= %s AND data_fim >= %s''',
                    (locacao_id, periodo_fim, periodo_ini))
        if cur.fetchone()[0] == 0:
            dias = (periodo_fim - periodo_ini).days + 1
            valor_prev = round(diaria_f * dias, 2)
            cur.execute('SELECT COALESCE(MAX(semana_numero),0)+1 FROM pagamentos_locacao WHERE locacao_id=%s', (locacao_id,))
            seq = cur.fetchone()[0]
            cur.execute('''INSERT INTO pagamentos_locacao
                               (locacao_id, semana_numero, data_inicio, data_fim,
                                valor_previsto, valor_pago, desconto, status)
                           VALUES (%s,%s,%s,%s,%s,0,0,'pendente')''',
                        (locacao_id, seq, periodo_ini, periodo_fim, valor_prev))
        periodo_ini = periodo_fim + timedelta(days=1)


@app.route('/api/locacoes', methods=['POST'])
@login_required
def add_locacao():
    data = request.json
    try:
        data_fim  = data.get('data_fim') or None
        diaria    = float(data.get('diaria') or 0)
        total     = None
        if data_fim:
            dias  = (datetime.strptime(data_fim, '%Y-%m-%d') -
                     datetime.strptime(data['data_inicio'], '%Y-%m-%d')).days + 1
            total = dias * diaria

        fotos_saida = data.get('fotos_saida')
        if fotos_saida and isinstance(fotos_saida, list):
            fotos_saida = json.dumps(fotos_saida)

        freq = data.get('frequencia_cobranca') or 'avulso'
        with _db() as (conn, cur):
            cur.execute('''
                INSERT INTO locacoes (veiculo_id, cliente_id, data_inicio, data_fim, diaria, total, km_saida, status, checklist, fotos_saida, observacoes, frequencia_cobranca)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            ''', (data['veiculo_id'], data['cliente_id'], data['data_inicio'], data_fim,
                  diaria or None, total, data.get('km_saida') or None, data.get('status', 'ativa'),
                  data.get('checklist'), fotos_saida, data.get('observacoes'), freq))
            new_id = cur.fetchone()[0]
            cur.execute("UPDATE veiculos SET status = 'locado' WHERE id = %s", (data['veiculo_id'],))
            if freq in ('semanal', 'quinzenal', 'mensal') and not data_fim:
                _gerar_periodos_futuros(cur, new_id, data['data_inicio'], freq, diaria)
        return jsonify({'success': True})
    except Exception as e:
        app.logger.error('Erro em add_locacao: %s', e)
        return jsonify({'error': 'Erro ao salvar locação. Tente novamente.'}), 500


@app.route('/api/locacoes/<int:id>', methods=['PUT'])
@login_required
def update_locacao(id):
    data = request.json
    try:
        with _db() as (conn, cur):
            cur.execute('SELECT veiculo_id, status FROM locacoes WHERE id = %s', (id,))
            locacao = cur.fetchone()
            if not locacao:
                return jsonify({'error': 'Locação não encontrada'}), 404

            veiculo_antigo, status_antigo = locacao

            fotos_saida = data.get('fotos_saida')
            if fotos_saida and isinstance(fotos_saida, list):
                fotos_saida = json.dumps(fotos_saida)

            # data_fim é opcional — converte string vazia para None
            data_fim = data.get('data_fim') or None
            total    = data.get('total')
            if total == '' or total is None:
                total = None

            cur.execute('''
                UPDATE locacoes SET veiculo_id=%s, cliente_id=%s, data_inicio=%s, data_fim=%s,
                diaria=%s, total=%s, km_saida=%s, checklist=%s, fotos_saida=%s, observacoes=%s,
                frequencia_cobranca=%s
                WHERE id=%s
            ''', (data.get('veiculo_id'), data.get('cliente_id'), data.get('data_inicio'),
                  data_fim, data.get('diaria'), total,
                  data.get('km_saida') or None, data.get('checklist'),
                  fotos_saida, data.get('observacoes'),
                  data.get('frequencia_cobranca') or 'avulso', id))

            veiculo_novo = data.get('veiculo_id')
            if veiculo_antigo != veiculo_novo and status_antigo == 'ativa':
                cur.execute("UPDATE veiculos SET status = 'disponivel' WHERE id = %s", (veiculo_antigo,))
                cur.execute("UPDATE veiculos SET status = 'locado' WHERE id = %s", (veiculo_novo,))

            freq_edit = data.get('frequencia_cobranca') or 'avulso'
            if freq_edit in ('semanal', 'quinzenal', 'mensal') and not data_fim:
                _gerar_periodos_futuros(cur, id, data.get('data_inicio'), freq_edit, data.get('diaria'))

    except Exception as e:
        app.logger.error('Erro em update_locacao: %s', e)
        return jsonify({'error': 'Erro ao atualizar locação. Tente novamente.'}), 500

    return jsonify({'success': True})


@app.route('/api/locacoes/<int:id>', methods=['DELETE'])
@login_required
def delete_locacao(id):
    if current_user.nivel != 'admin':
        return jsonify({'error': 'Acesso negado'}), 403
    try:
        with _db() as (conn, cur):
            cur.execute('SELECT veiculo_id, status FROM locacoes WHERE id=%s', (id,))
            row = cur.fetchone()
            if not row:
                return jsonify({'error': 'Locação não encontrada'}), 404
            veiculo_id, status_loc = row
            if status_loc == 'ativa' and veiculo_id:
                cur.execute("UPDATE veiculos SET status='disponivel' WHERE id=%s", (veiculo_id,))
            cur.execute('DELETE FROM locacoes WHERE id=%s', (id,))
        return jsonify({'success': True})
    except Exception as e:
        app.logger.error('Erro em delete_locacao: %s', e)
        return jsonify({'error': 'Erro interno. Tente novamente.'}), 500


@app.route('/api/locacoes/<int:id>/devolver', methods=['PUT'])
@login_required
def devolver_locacao(id):
    data = request.json
    with _db() as (conn, cur):
        cur.execute('SELECT veiculo_id FROM locacoes WHERE id = %s', (id,))
        locacao = cur.fetchone()

        if locacao:
            fotos_retorno = data.get('fotos_retorno')
            if fotos_retorno and isinstance(fotos_retorno, list):
                fotos_retorno = json.dumps(fotos_retorno)
            cur.execute(
                "UPDATE locacoes SET data_fim=%s, data_devolucao_real=%s, km_retorno=%s, fotos_retorno=%s, status='concluida' WHERE id=%s",
                (data.get('data_fim'), data.get('data_devolucao_real'), data.get('km_retorno'), fotos_retorno, id)
            )
            if data.get('km_retorno'):
                cur.execute(
                    "UPDATE veiculos SET km_atual=%s, status='disponivel' WHERE id=%s",
                    (data['km_retorno'], locacao[0])
                )

    return jsonify({'success': True})

# ==================== API ABASTECIMENTOS ====================

@app.route('/api/abastecimentos', methods=['GET'])
@login_required
def get_abastecimentos():
    with _db() as (conn, cur):
        cur.execute('''
            SELECT a.*, v.placa, v.marca, v.modelo
            FROM abastecimentos a
            LEFT JOIN veiculos v ON a.veiculo_id = v.id
            ORDER BY a.data_abastecimento DESC
        ''')
        result = rows_to_dict(cur)
    return jsonify(result)


@app.route('/api/abastecimentos', methods=['POST'])
@login_required
def add_abastecimento():
    data = request.json
    total = float(data.get('litros', 0)) * float(data.get('valor_litro', 0))
    with _db() as (conn, cur):
        cur.execute('''
            INSERT INTO abastecimentos (veiculo_id, data_abastecimento, litros, valor_litro, total, km_abastecimento)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (data['veiculo_id'], data.get('data_abastecimento'), data.get('litros'),
              data.get('valor_litro'), total, data.get('km_abastecimento')))

        if data.get('km_abastecimento'):
            cur.execute('UPDATE veiculos SET km_atual = %s WHERE id = %s',
                        (data['km_abastecimento'], data['veiculo_id']))

    return jsonify({'success': True})


@app.route('/api/abastecimentos/<int:id>', methods=['DELETE'])
@login_required
def delete_abastecimento(id):
    with _db() as (conn, cur):
        cur.execute('DELETE FROM abastecimentos WHERE id=%s', (id,))
    return jsonify({'success': True})

# ==================== API MULTAS ====================

@app.route('/api/multas/importar-pdf', methods=['POST'])
@login_required
def importar_multa_pdf():
    import unicodedata
    f = request.files.get('pdf')
    if not f:
        return jsonify({'error': 'Nenhum arquivo enviado'}), 400
    try:
        with pdfplumber.open(io.BytesIO(f.read())) as pdf:
            text = '\n'.join(page.extract_text() or '' for page in pdf.pages)
        text = unicodedata.normalize('NFC', text)
    except Exception as e:
        return jsonify({'error': f'Erro ao ler PDF: {e}'}), 400

    # Abordagem linha-a-linha: mais robusta para PDFs multi-coluna do DENATRAN
    lines = [l.strip() for l in text.splitlines()]

    def after_label(label_re, value_re=None, default=''):
        """Retorna valor na linha seguinte ao label (ou na mesma linha após o label).
        Varre até 6 linhas seguintes sem break prematuro."""
        for i, line in enumerate(lines):
            if not re.search(label_re, line, re.IGNORECASE):
                continue
            # Tenta extrair da mesma linha (após o label)
            rest = re.split(label_re, line, maxsplit=1, flags=re.IGNORECASE)[-1].strip()
            if rest:
                if value_re is None:
                    return rest
                m = re.search(value_re, rest, re.IGNORECASE)
                if m:
                    return m.group(0).strip()
            # Busca nas próximas linhas — sem break, varre todas as 6
            for j in range(i + 1, min(i + 7, len(lines))):
                v = lines[j].strip()
                if not v:
                    continue
                if value_re is None:
                    return v
                m = re.search(value_re, v, re.IGNORECASE)
                if m:
                    return m.group(0).strip()
        return default

    def to_iso(d):
        if not d: return ''
        try:
            p = d.strip().split('/')
            return f'{p[2]}-{p[1]}-{p[0]}'
        except: return ''

    # AIT: linha tem "AIT)" + "CURITIBA PR" na mesma linha — pegar próxima linha que tem letras+dígitos
    ait = after_label(r'IDENTIFICA\w*\s+DO\s+AUTO.*AIT\)', value_re=r'[A-Z]{1,5}\d{5,}')
    tipo = 'nic' if ait.upper().startswith('NIC') else 'multa'

    # PLACA: linha é "PLACA ESPÉCIE PAÍS" — não exata, pega próxima linha com placa 7 chars
    placa = after_label(r'\bPLACA\b', value_re=r'\b[A-Z0-9]{7}\b')

    # HORA: "01:10" aparece misturado na linha de órgão competente — busca direto no texto
    hora_m = re.search(r'\b(\d{2}:\d{2})\b', text)
    hora = hora_m.group(1) if hora_m else ''

    # LOCAL: vários formatos possíveis — tenta em ordem de especificidade
    local_raw = after_label(r'LOCAL\s+DA\s+INFRA\w+')
    local = ''
    if local_raw:
        # 1. CIDADE-UF (ex: CURITIBA-PR)
        m = re.search(r'\b([A-ZÁÉÍÓÚÃÕÀÂÊÎÔÛÇ][A-ZÁÉÍÓÚÃÕÀÂÊÎÔÛÇ ]*-[A-Z]{2})\b', local_raw)
        if m:
            local = m.group(1).strip()
        else:
            # 2. Rodovia federal/estadual (ex: BR476, PR092)
            m2 = re.search(r'\b((?:BR|PR|SP|MG|RS|SC|BA|RJ|GO|MT|MS|TO|CE|PE|MA|PI)\d+\b.*)', local_raw, re.IGNORECASE)
            if m2:
                local = m2.group(1).strip()
            else:
                # 3. Keyword de logradouro (SITIO, AEROPORTO, AV, RUA, ROD...)
                m3 = re.search(r'\b((?:SITIO|AEROPORTO|TERMINAL|AV|RUA|ROD|RODOV|ESTRADA|PRACA|VIADUTO)\b.*)', local_raw, re.IGNORECASE)
                if m3:
                    local = m3.group(1).strip()
                else:
                    # 4. Fallback: remove prefixo numérico de código de órgão
                    local = re.sub(r'^\d+\s+', '', local_raw).strip()

    # VALOR: próxima linha após "VALOR DA MULTA"
    valor_raw = after_label(r'VALOR\s+DA\s+MULTA', value_re=r'[\d]+[.,][\d]+')
    valor_num = re.search(r'[\d.,]+', valor_raw) if valor_raw else None
    valor = valor_num.group(0).replace('.', '').replace(',', '.') if valor_num else ''

    # DESCRIÇÃO: linhas "DATA LIMITE ... INFRATOR <descrição>" têm a desc na segunda parte
    descricao_lines = []
    in_desc = False
    for line in lines:
        if re.search(r'DESCRI\w+\s+DA\s+INFRA\w+', line, re.IGNORECASE):
            in_desc = True
            continue
        if in_desc:
            if re.search(r'MEDI\w+\s+REALIZADA|VALOR\s+CONSIDERADO', line, re.IGNORECASE):
                break
            # Pula linhas vazias, "Não se aplica" e datas soltas
            if not line or re.search(r'^N[ãa]o\s+se\s+aplica|\b\d{2}/\d{2}/\d{4}\b$', line, re.IGNORECASE):
                continue
            # Linha "DATA LIMITE PARA ... INFRATOR <desc>" — extrai só a parte da descrição
            dl = re.search(r'^DATA\s+LIMITE\s+PARA\s+.+?(?:INFRATOR|PRÉVIA)\s+(.+)', line, re.IGNORECASE)
            if dl:
                resto = dl.group(1).strip()
                if resto:
                    descricao_lines.append(resto)
            elif not re.search(r'^DATA\s+LIMITE', line, re.IGNORECASE):
                descricao_lines.append(line)
    descricao = ' '.join(descricao_lines)

    # RENAINF: linha "QNC7B48 ... NÚMERO RENAINF NÚMERO RENAINF MULTA ORIGINAL"
    # valor na próxima linha: "11420353810 11211914720"
    renainf = after_label(r'RENAINF(?!\s+MULTA)', value_re=r'\d{7,}')

    dt_limite = after_label(r'DATA\s+LIMITE.*DEFESA', value_re=r'\d{2}/\d{2}/\d{4}')
    # DATA DA NOTIFICAÇÃO DA AUTUAÇÃO = "Data da Infração" no sistema
    dt_notif  = after_label(r'DATA\s+DA\s+NOTIFICA\w+\s+DA\s+AUTUA', value_re=r'\d{2}/\d{2}/\d{4}')

    return jsonify({
        'tipo_notificacao':   tipo,
        'numero_auto':        ait,
        'placa':              placa,
        'data_infracao':      to_iso(dt_notif),   # notificação = infração no sistema
        'hora_infracao':      hora,
        'local_infracao':     local,
        'valor':              valor,
        'descricao':          descricao,
        'numero_renainf':     renainf,
        'data_limite_defesa': to_iso(dt_limite),
        **(({'_debug': text[:3000]} if app.debug else {})),
    })


@app.route('/api/multas', methods=['GET'])
@login_required
def get_multas():
    with _db() as (conn, cur):
        cur.execute('''
            SELECT m.*, v.placa, v.marca, v.modelo, c.nome as nome_motorista
            FROM multas m
            LEFT JOIN veiculos v ON m.veiculo_id = v.id
            LEFT JOIN clientes c ON m.motorista_id = c.id
            ORDER BY m.data_infracao DESC
        ''')
        result = rows_to_dict(cur)
    return jsonify(result)


@app.route('/api/multas', methods=['POST'])
@login_required
def add_multa():
    data = request.json
    with _db() as (conn, cur):
        cur.execute('''
            INSERT INTO multas (veiculo_id, motorista_id, data_infracao, descricao, valor, local_infracao, pontos, status, observacoes, numero_auto, tipo_notificacao, data_limite_defesa, numero_renainf, hora_infracao)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (data.get('veiculo_id') or None, data.get('motorista_id') or None,
              data.get('data_infracao'), data.get('descricao'), data.get('valor'),
              data.get('local_infracao'), data.get('pontos') or None,
              data.get('status', 'pendente'), data.get('observacoes'),
              data.get('numero_auto'), data.get('tipo_notificacao', 'multa'),
              data.get('data_limite_defesa') or None, data.get('numero_renainf') or None,
              data.get('hora_infracao') or None))
    return jsonify({'success': True})


@app.route('/api/multas/<int:id>', methods=['PUT'])
@login_required
def update_multa(id):
    data = request.json
    with _db() as (conn, cur):
        cur.execute('''
            UPDATE multas SET veiculo_id=%s, motorista_id=%s, data_infracao=%s, descricao=%s,
            valor=%s, local_infracao=%s, pontos=%s, status=%s, observacoes=%s, numero_auto=%s,
            data_pagamento=%s, tipo_notificacao=%s, data_limite_defesa=%s, numero_renainf=%s, hora_infracao=%s
            WHERE id=%s
        ''', (data.get('veiculo_id'), data.get('motorista_id'), data.get('data_infracao'),
              data.get('descricao'), data.get('valor'), data.get('local_infracao'),
              data.get('pontos'), data.get('status'), data.get('observacoes'),
              data.get('numero_auto'), data.get('data_pagamento'),
              data.get('tipo_notificacao', 'multa'), data.get('data_limite_defesa') or None,
              data.get('numero_renainf') or None, data.get('hora_infracao') or None, id))
    return jsonify({'success': True})


@app.route('/api/multas/<int:id>', methods=['DELETE'])
@login_required
def delete_multa(id):
    if current_user.nivel != 'admin':
        return jsonify({'error': 'Acesso negado'}), 403
    with _db() as (conn, cur):
        cur.execute('DELETE FROM multas WHERE id=%s', (id,))
    return jsonify({'success': True})


@app.route('/api/buscar-multas', methods=['POST'])
@login_required
def buscar_multas_online():
    data = request.json
    placa = data.get('placa', '').upper().replace('-', '').replace(' ', '')
    renavam = data.get('renavam', '').strip()
    if not placa:
        return jsonify({'error': 'Placa não informada'}), 400

    consumer_key = os.environ.get('SERPRO_CONSUMER_KEY', '')
    consumer_secret = os.environ.get('SERPRO_CONSUMER_SECRET', '')

    if not consumer_key or not consumer_secret:
        return jsonify({
            'success': False,
            'error': 'Credenciais SERPRO não configuradas. Adicione SERPRO_CONSUMER_KEY e SERPRO_CONSUMER_SECRET no .env'
        }), 503

    try:
        # 1. Obter token JWT do SERPRO
        credenciais_b64 = base64.b64encode(f"{consumer_key}:{consumer_secret}".encode()).decode()
        auth_resp = requests.post(
            'https://autenticacao.sapi.serpro.gov.br/authenticate',
            headers={
                'Authorization': f'Basic {credenciais_b64}',
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            data={'grant_type': 'client_credentials'},
            timeout=15
        )
        if auth_resp.status_code != 200:
            return jsonify({'success': False, 'error': f'Falha na autenticação SERPRO ({auth_resp.status_code})'}), 502

        token = auth_resp.json().get('access_token')
        headers_api = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}

        # 2. Consultar infrações pela placa (SENATRAN via SERPRO)
        url = f'https://apigateway.serpro.gov.br/consulta-veiculos/1/veiculos/{placa}/infracoes'
        resp = requests.get(url, headers=headers_api, timeout=15)

        if resp.status_code == 200:
            infracoes = resp.json() if resp.text else []
            if not isinstance(infracoes, list):
                infracoes = infracoes.get('infracoes', [infracoes])
            return jsonify({'success': True, 'placa': placa, 'infracoes': infracoes, 'total': len(infracoes)})
        elif resp.status_code == 404:
            return jsonify({'success': True, 'placa': placa, 'infracoes': [], 'total': 0, 'mensagem': 'Nenhuma infração encontrada para esta placa.'})
        else:
            return jsonify({'success': False, 'error': f'SERPRO retornou status {resp.status_code}: {resp.text[:200]}'}), 502

    except requests.Timeout:
        return jsonify({'success': False, 'error': 'Tempo de conexão com SERPRO excedido. Tente novamente.'}), 504
    except Exception as e:
        app.logger.error('Erro em buscar_multas_online: %s', e)
        return jsonify({'success': False, 'error': 'Erro interno. Tente novamente.'}), 500


_APIBRASIL_ESTADOS = ['MG','AL','PB','GO','MS','RR','PE','MA','TO','PA','PI','AM','SC','SE','PR']

def _apibrasil_consultar_estado(url_base, token, estado, placa, renavam):
    try:
        resp = requests.post(
            f'{url_base}/multas/{estado.lower()}',
            json={'placa': placa, 'renavam': renavam, 'token': token},
            timeout=20
        )
        resultado = resp.json()
        lista = resultado if isinstance(resultado, list) else resultado.get('multas', resultado.get('data', []))
        if not isinstance(lista, list):
            return []
        for m in lista:
            m['_estado'] = estado
        return lista
    except Exception:
        return []

@app.route('/api/consultar-multas-apibrasil', methods=['POST'])
@login_required
def consultar_multas_apibrasil():
    from concurrent.futures import ThreadPoolExecutor, as_completed
    url_base = os.environ.get('APIBRASIL_MULTAS_URL', '').rstrip('/')
    token    = os.environ.get('APIBRASIL_MULTAS_TOKEN', '')

    if not url_base or not token:
        return jsonify({'error': 'APIBrasil não configurada. Adicione APIBRASIL_MULTAS_URL e APIBRASIL_MULTAS_TOKEN nas variáveis de ambiente do Railway.'}), 503

    body    = request.json
    placa   = re.sub(r'[^A-Z0-9]', '', (body.get('placa') or '').upper())
    renavam = re.sub(r'\D', '', body.get('renavam') or '')
    estado  = (body.get('estado') or '').upper()

    if not placa:
        return jsonify({'error': 'Placa não informada'}), 400

    estados = [estado] if estado else _APIBRASIL_ESTADOS

    try:
        multas_lista = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            futuros = {ex.submit(_apibrasil_consultar_estado, url_base, token, uf, placa, renavam): uf for uf in estados}
            for fut in as_completed(futuros):
                multas_lista.extend(fut.result())
        return jsonify({'success': True, 'multas': multas_lista, 'total': len(multas_lista), 'estados_consultados': len(estados)})
    except requests.exceptions.ConnectionError:
        return jsonify({'error': f'Não foi possível conectar ao servidor APIBrasil ({url_base}).'}), 503
    except Exception as e:
        app.logger.error('Erro em consultar_multas_apibrasil: %s', e)
        return jsonify({'error': 'Erro interno. Tente novamente.'}), 500

# ==================== API FORNECEDORES ====================

@app.route('/api/fornecedores', methods=['GET'])
@login_required
def get_fornecedores():
    with _db() as (conn, cur):
        cur.execute('''
            SELECT f.*, t.nome as tipo_nome
            FROM fornecedores f
            LEFT JOIN tipos_fornecedor t ON f.tipo_id = t.id
            ORDER BY f.nome
        ''')
        result = rows_to_dict(cur)
    return jsonify(result)


@app.route('/api/fornecedores', methods=['POST'])
@login_required
def add_fornecedor():
    data = request.json
    with _db() as (conn, cur):
        cur.execute('''
            INSERT INTO fornecedores (nome, cnpj, cpf, telefone, email, endereco, bairro, cep, cidade, estado,
                tipo_id, responsavel, nome_fantasia, data_fundacao, situacao_receita, status, observacoes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (data['nome'], data.get('cnpj'), data.get('cpf'), data.get('telefone'),
              data.get('email'), data.get('endereco'), data.get('bairro'), data.get('cep'),
              data.get('cidade'), data.get('estado'), data.get('tipo_id'), data.get('responsavel'),
              data.get('nome_fantasia'), data.get('data_fundacao') or None,
              data.get('situacao_receita'), data.get('status', 'ativo'), data.get('observacoes')))
    return jsonify({'success': True})


@app.route('/api/fornecedores/<int:id>', methods=['PUT'])
@login_required
def update_fornecedor(id):
    data = request.json
    with _db() as (conn, cur):
        cur.execute('''
            UPDATE fornecedores SET nome=%s, cnpj=%s, cpf=%s, telefone=%s, email=%s,
            endereco=%s, bairro=%s, cep=%s, cidade=%s, estado=%s, tipo_id=%s, responsavel=%s,
            nome_fantasia=%s, data_fundacao=%s, situacao_receita=%s, status=%s, observacoes=%s WHERE id=%s
        ''', (data['nome'], data.get('cnpj'), data.get('cpf'), data.get('telefone'),
              data.get('email'), data.get('endereco'), data.get('bairro'), data.get('cep'),
              data.get('cidade'), data.get('estado'), data.get('tipo_id'), data.get('responsavel'),
              data.get('nome_fantasia'), data.get('data_fundacao') or None,
              data.get('situacao_receita'), data.get('status'), data.get('observacoes'), id))
    return jsonify({'success': True})


@app.route('/api/fornecedores/<int:id>', methods=['DELETE'])
@login_required
def delete_fornecedor(id):
    with _db() as (conn, cur):
        cur.execute('DELETE FROM fornecedores WHERE id=%s', (id,))
    return jsonify({'success': True})


@app.route('/api/tipos-fornecedor', methods=['GET'])
@login_required
def get_tipos_fornecedor():
    with _db() as (conn, cur):
        cur.execute('SELECT * FROM tipos_fornecedor ORDER BY nome')
        result = rows_to_dict(cur)
    return jsonify(result)


@app.route('/api/tipos-fornecedor', methods=['POST'])
@login_required
def add_tipo_fornecedor():
    data = request.json
    with _db() as (conn, cur):
        cur.execute('INSERT INTO tipos_fornecedor (nome, descricao) VALUES (%s, %s)',
                    (data['nome'], data.get('descricao')))
    return jsonify({'success': True})


@app.route('/api/tipos-fornecedor/<int:id>', methods=['DELETE'])
@login_required
def delete_tipo_fornecedor(id):
    with _db() as (conn, cur):
        cur.execute('DELETE FROM tipos_fornecedor WHERE id=%s', (id,))
    return jsonify({'success': True})

# ==================== API CONSULTA CNPJ ====================

@app.route('/api/consulta-cnpj/<cnpj>')
@login_required
def consulta_cnpj(cnpj):
    cnpj_clean = re.sub(r'\D', '', cnpj)
    if len(cnpj_clean) != 14:
        return jsonify({'error': 'CNPJ inválido'}), 400
    try:
        resp = requests.get(
            f'https://brasilapi.com.br/api/cnpj/v1/{cnpj_clean}',
            timeout=10,
            headers={'Accept': 'application/json'}
        )
        if resp.status_code == 200:
            d = resp.json()

            def fmt_fone(raw):
                n = re.sub(r'\D', '', raw or '')
                if len(n) == 10: return f'({n[:2]}) {n[2:6]}-{n[6:]}'
                if len(n) == 11: return f'({n[:2]}) {n[2:7]}-{n[7:]}'
                return n

            def fmt_cep(raw):
                n = re.sub(r'\D', '', raw or '')
                return f'{n[:5]}-{n[5:]}' if len(n) == 8 else n

            endereco_parts = [
                d.get('logradouro', ''),
                d.get('numero', ''),
                d.get('complemento', '')
            ]
            endereco = ', '.join(p for p in endereco_parts if p and p.strip())

            data_fundacao = None
            if d.get('data_inicio_atividade'):
                try:
                    data_fundacao = str(d['data_inicio_atividade'])[:10]
                except Exception:
                    pass

            socio_principal = ''
            qsa = d.get('qsa') or []
            if qsa:
                socio_principal = qsa[0].get('nome_socio', '') or qsa[0].get('nome_representante_legal', '')

            situacao = d.get('descricao_situacao_cadastral', '')

            return jsonify({
                'nome': d.get('razao_social') or '',
                'nome_fantasia': d.get('nome_fantasia') or '',
                'email': (d.get('email') or '').lower(),
                'telefone': fmt_fone(d.get('ddd_telefone_1', '')),
                'telefone2': fmt_fone(d.get('ddd_telefone_2', '')),
                'endereco': endereco,
                'bairro': d.get('bairro') or '',
                'cidade': d.get('municipio') or '',
                'estado': d.get('uf') or '',
                'cep': fmt_cep(d.get('cep', '')),
                'data_fundacao': data_fundacao,
                'responsavel_principal': socio_principal,
                'situacao_receita': situacao,
                'cnpj_formatado': f'{cnpj_clean[:2]}.{cnpj_clean[2:5]}.{cnpj_clean[5:8]}/{cnpj_clean[8:12]}-{cnpj_clean[12:]}',
                'atividade': d.get('cnae_fiscal_descricao') or '',
                'porte': d.get('descricao_porte') or '',
                'simples': d.get('opcao_pelo_simples', False),
            })
        return jsonify({'error': 'CNPJ não encontrado na Receita Federal'}), 404
    except requests.exceptions.Timeout:
        return jsonify({'error': 'Tempo esgotado na consulta'}), 504
    except Exception as e:
        return jsonify({'error': 'Erro ao consultar CNPJ'}), 500

# ==================== API DASHBOARD ====================

@app.route('/api/dashboard')
@login_required
def get_dashboard():
    with _db() as (conn, cur):
        cur.execute('SELECT COUNT(*) FROM veiculos')
        total_veiculos = cur.fetchone()[0]

        cur.execute("SELECT status, COUNT(*) FROM veiculos GROUP BY status")
        status_veiculos = {row[0]: row[1] for row in cur.fetchall()}
        disponiveis = status_veiculos.get('disponivel', 0)
        taxa_ocupacao = ((total_veiculos - disponiveis) / total_veiculos * 100) if total_veiculos > 0 else 0

        cur.execute("SELECT COUNT(*) FROM clientes WHERE status = 'ativo'")
        total_clientes = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM locacoes WHERE status = 'ativa'")
        locacoes_ativas = cur.fetchone()[0]

        mes_atual = datetime.now().strftime('%Y-%m')
        cur.execute(
            "SELECT COALESCE(SUM(total), 0) FROM locacoes WHERE TO_CHAR(data_inicio, 'YYYY-MM') = %s",
            (mes_atual,)
        )
        faturamento_mes = float(cur.fetchone()[0])

        cur.execute("SELECT COALESCE(SUM(total), 0) FROM locacoes WHERE status = 'concluida'")
        faturamento_total = float(cur.fetchone()[0])

        cur.execute("SELECT COALESCE(SUM(custo), 0) FROM manutencoes WHERE status = 'concluida'")
        custo_manutencao = float(cur.fetchone()[0])

        cur.execute('''
            SELECT v.placa, v.marca, v.modelo, COUNT(l.id) as total_locacoes
            FROM veiculos v
            LEFT JOIN locacoes l ON v.id = l.veiculo_id
            GROUP BY v.id, v.placa, v.marca, v.modelo
            ORDER BY total_locacoes DESC
            LIMIT 5
        ''')
        veiculos_mais_locados = [
            {'placa': r[0], 'marca': r[1], 'modelo': r[2], 'total_locacoes': r[3]}
            for r in cur.fetchall()
        ]

        cur.execute('''
            SELECT TO_CHAR(data_inicio, 'YYYY-MM') as mes, SUM(total) as total, COUNT(*) as qtd
            FROM locacoes WHERE status = 'concluida'
            GROUP BY mes ORDER BY mes DESC LIMIT 6
        ''')
        locacoes_mes_raw = cur.fetchall()

        locacoes_mes = []
        for row in locacoes_mes_raw:
            mes = row[0]
            cur.execute(
                "SELECT COALESCE(SUM(custo), 0) FROM manutencoes WHERE TO_CHAR(data_manutencao, 'YYYY-MM') = %s AND status = 'concluida'",
                (mes,)
            )
            custo_m = float(cur.fetchone()[0])
            locacoes_mes.append({'mes': mes, 'total': float(row[1] or 0), 'qtd': row[2], 'custo_manutencao': custo_m})

        cur.execute("SELECT COUNT(*) FROM manutencoes WHERE status != 'concluida'")
        manutencoes_pendentes = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM multas WHERE status = 'pendente'")
        multas_pendentes = cur.fetchone()[0]

    return jsonify({
        'total_veiculos': total_veiculos,
        'veiculos_disponiveis': disponiveis,
        'veiculos_locados': status_veiculos.get('locado', 0),
        'veiculos_manutencao': status_veiculos.get('manutencao', 0),
        'taxa_ocupacao': round(taxa_ocupacao, 1),
        'total_clientes': total_clientes,
        'locacoes_ativas': locacoes_ativas,
        'faturamento_mes': round(faturamento_mes, 2),
        'faturamento_total': round(faturamento_total, 2),
        'custo_manutencao': round(custo_manutencao, 2),
        'veiculos_mais_locados': veiculos_mais_locados,
        'locacoes_mes': locacoes_mes,
        'manutencoes_pendentes': manutencoes_pendentes,
        'multas_pendentes': multas_pendentes,
    })

# ==================== API CUSTOS POR VEÍCULO ====================

@app.route('/api/custos-veiculos')
@login_required
def get_custos_veiculos():
    with _db() as (conn, cur):
        cur.execute('''
            SELECT v.id, v.placa, v.marca, v.modelo, v.ano, v.km_atual,
                   COALESCE(SUM(m.custo), 0) as custo_total_manutencao
            FROM veiculos v
            LEFT JOIN manutencoes m ON v.id = m.veiculo_id AND m.status = 'concluida'
            GROUP BY v.id, v.placa, v.marca, v.modelo, v.ano, v.km_atual
            HAVING COALESCE(SUM(m.custo), 0) > 0
            ORDER BY v.placa
        ''')
        rows = cur.fetchall()

    veiculos = []
    for r in rows:
        km = r[5] or 1
        custo = float(r[6] or 0)
        veiculos.append({
            'id': r[0], 'placa': r[1], 'marca': r[2], 'modelo': r[3],
            'ano': r[4], 'km_atual': km,
            'custo_total': round(custo, 2),
            'custo_por_km': round(custo / km, 4) if km > 0 else 0
        })

    veiculos.sort(key=lambda x: x['custo_por_km'], reverse=True)
    return jsonify(veiculos)

# ==================== API RELATÓRIOS ====================

@app.route('/api/relatorio-lucratividade', methods=['POST'])
@login_required
def get_relatorio_lucratividade():
    try:
        data = request.json
        veiculos_ids = data.get('veiculos', [])
        data_inicio = data.get('data_inicio')
        data_fim = data.get('data_fim')
        inc_manut = data.get('incluir_manutencao', True)
        inc_abast = data.get('incluir_abastecimento', True)
        inc_multas = data.get('incluir_multas', True)

        if not veiculos_ids or not data_inicio or not data_fim:
            return jsonify({'error': 'Parâmetros inválidos'}), 400

        # Gera lista de meses do período diretamente — não depende de locações
        def _meses_periodo(ini, fim):
            try:
                c = _date.fromisoformat(ini).replace(day=1)
                e = _date.fromisoformat(fim)
                acc = []
                while c <= e:
                    acc.append(c.strftime('%Y-%m'))
                    m2 = c.month + 1
                    c = c.replace(year=c.year + (1 if m2 > 12 else 0), month=(1 if m2 > 12 else m2))
                return acc
            except Exception:
                return []

        meses = _meses_periodo(data_inicio, data_fim)

        with _db() as (conn, cur):
            resultados = []
            for vid in veiculos_ids:
                cur.execute('SELECT * FROM veiculos WHERE id = %s', (vid,))
                cols = [d[0] for d in cur.description]
                vrow = cur.fetchone()
                if not vrow:
                    continue
                v = dict(zip(cols, vrow))

                cur.execute('''
                    SELECT COALESCE(SUM(total), 0), COUNT(*)
                    FROM locacoes WHERE veiculo_id=%s AND data_inicio>=%s AND data_inicio<=%s
                ''', (vid, data_inicio, data_fim))
                rec_row = cur.fetchone()
                receita = float(rec_row[0])

                custo_m = custo_a = custo_mul = 0.0

                if inc_manut:
                    cur.execute('SELECT COALESCE(SUM(custo),0) FROM manutencoes WHERE veiculo_id=%s AND data_manutencao>=%s AND data_manutencao<=%s', (vid, data_inicio, data_fim))
                    custo_m = float(cur.fetchone()[0])

                if inc_abast:
                    cur.execute('SELECT COALESCE(SUM(total),0) FROM abastecimentos WHERE veiculo_id=%s AND data_abastecimento>=%s AND data_abastecimento<=%s', (vid, data_inicio, data_fim))
                    custo_a = float(cur.fetchone()[0])

                if inc_multas:
                    cur.execute('SELECT COALESCE(SUM(valor),0) FROM multas WHERE veiculo_id=%s AND data_infracao>=%s AND data_infracao<=%s', (vid, data_inicio, data_fim))
                    custo_mul = float(cur.fetchone()[0])

                total_custos = custo_m + custo_a + custo_mul
                lucro = receita - total_custos

                resultados.append({
                    'veiculo': {'id': v['id'], 'placa': v['placa'], 'marca': v['marca'], 'modelo': v['modelo'], 'ano': v['ano'] or 0},
                    'receita': round(receita, 2),
                    'custo_manutencao': round(custo_m, 2),
                    'custo_abastecimento': round(custo_a, 2),
                    'custo_multas': round(custo_mul, 2),
                    'total_custos': round(total_custos, 2),
                    'lucro': round(lucro, 2),
                    'margem': round(lucro / receita * 100, 2) if receita > 0 else 0
                })

            dados_mensais = []
            for mes in meses:
                cur.execute("SELECT COALESCE(SUM(total),0) FROM locacoes WHERE TO_CHAR(data_inicio,'YYYY-MM')=%s", (mes,))
                mr = float(cur.fetchone()[0])
                mm = ma = mmul = 0.0
                if inc_manut:
                    cur.execute("SELECT COALESCE(SUM(custo),0) FROM manutencoes WHERE TO_CHAR(data_manutencao,'YYYY-MM')=%s", (mes,))
                    mm = float(cur.fetchone()[0])
                if inc_abast:
                    cur.execute("SELECT COALESCE(SUM(total),0) FROM abastecimentos WHERE TO_CHAR(data_abastecimento,'YYYY-MM')=%s", (mes,))
                    ma = float(cur.fetchone()[0])
                if inc_multas:
                    cur.execute("SELECT COALESCE(SUM(valor),0) FROM multas WHERE TO_CHAR(data_infracao,'YYYY-MM')=%s AND status='pago'", (mes,))
                    mmul = float(cur.fetchone()[0])
                dados_mensais.append({'mes': mes, 'receita': round(mr, 2), 'custos': round(mm + ma + mmul, 2), 'lucro': round(mr - mm - ma - mmul, 2)})

        return jsonify({'resultados': resultados, 'dados_mensais': dados_mensais, 'periodo': {'inicio': data_inicio, 'fim': data_fim}})

    except Exception as e:
        app.logger.error('Erro em get_relatorio_lucratividade: %s', e)
        return jsonify({'error': 'Erro interno. Tente novamente.'}), 500


@app.route('/api/relatorio-fornecedor', methods=['POST'])
@login_required
def get_relatorio_fornecedor():
    try:
        data = request.json
        fornecedor_id = data.get('fornecedor_id')
        data_inicio = data.get('data_inicio')
        data_fim = data.get('data_fim')

        if not data_inicio or not data_fim:
            return jsonify({'error': 'Parâmetros inválidos'}), 400

        with _db() as (conn, cur):
            if not fornecedor_id:
                # Agrupa por fornecedor cadastrado (via fornecedor_id) ou por nome da oficina (texto livre)
                cur.execute('''
                    SELECT
                        COALESCE(f.nome, NULLIF(m.oficina,''), 'Sem fornecedor') AS nome,
                        COUNT(*)                          AS qtd,
                        COALESCE(SUM(m.custo), 0)         AS total
                    FROM manutencoes m
                    LEFT JOIN fornecedores f ON f.id = m.fornecedor_id
                    WHERE m.data_manutencao >= %s AND m.data_manutencao <= %s
                      AND COALESCE(m.custo, 0) > 0
                    GROUP BY COALESCE(f.nome, NULLIF(m.oficina,''), 'Sem fornecedor')
                    ORDER BY total DESC
                ''', (data_inicio, data_fim))
                rows = cur.fetchall()
                resultados = [{'nome': r[0], 'qtd_servicos': r[1], 'total_gasto': round(float(r[2]), 2)} for r in rows]
            else:
                cur.execute('SELECT nome FROM fornecedores WHERE id=%s', (fornecedor_id,))
                f = cur.fetchone()
                nome = f[0] if f else ''
                # Busca por fornecedor_id OU por nome no campo oficina (retrocompatibilidade)
                cur.execute('''
                    SELECT COUNT(*), COALESCE(SUM(custo), 0) FROM manutencoes
                    WHERE (fornecedor_id = %s OR oficina = %s)
                      AND data_manutencao >= %s AND data_manutencao <= %s
                ''', (fornecedor_id, nome, data_inicio, data_fim))
                r = cur.fetchone()
                resultados = [{'id': fornecedor_id, 'nome': nome, 'qtd_servicos': r[0], 'total_gasto': round(float(r[1]), 2)}]

        return jsonify({'resultados': resultados, 'periodo': {'inicio': data_inicio, 'fim': data_fim}})

    except Exception as e:
        app.logger.error('Erro em get_relatorio_fornecedor: %s', e)
        return jsonify({'error': 'Erro interno. Tente novamente.'}), 500

# ==================== API ALERTAS ====================

@app.route('/api/alertas-manutencao')
@login_required
def get_alertas_manutencao():
    with _db() as (conn, cur):
        cur.execute('''
            SELECT v.id, v.placa, v.marca, v.modelo, v.km_atual,
                   m.proxima_manutencao_km, m.proxima_manutencao_data, m.tipo,
                   (m.proxima_manutencao_km - v.km_atual) as km_restante
            FROM veiculos v
            LEFT JOIN LATERAL (
                SELECT proxima_manutencao_km, proxima_manutencao_data, tipo
                FROM manutencoes
                WHERE veiculo_id = v.id
                  AND proxima_manutencao_km IS NOT NULL
                  AND proxima_manutencao_km > v.km_atual
                ORDER BY proxima_manutencao_km ASC
                LIMIT 1
            ) m ON TRUE
            WHERE m.proxima_manutencao_km IS NOT NULL
              AND (m.proxima_manutencao_km - v.km_atual) <= 1000
            ORDER BY km_restante ASC
        ''')
        alertas = rows_to_dict(cur)
    return jsonify(alertas)


# ==================== API CLIENTES EXTRA ====================

@app.route('/api/clientes/<int:id>/locacoes')
@login_required
def get_locacoes_cliente(id):
    with _db() as (conn, cur):
        cur.execute('''
            SELECT l.*, v.placa, v.marca, v.modelo
            FROM locacoes l
            LEFT JOIN veiculos v ON l.veiculo_id = v.id
            WHERE l.cliente_id = %s
            ORDER BY l.data_inicio DESC
        ''', (id,))
        result = rows_to_dict(cur)
    return jsonify(result)


@app.route('/api/clientes/<int:id>', methods=['GET'])
@login_required
def get_cliente(id):
    with _db() as (conn, cur):
        cur.execute('SELECT * FROM clientes WHERE id = %s', (id,))
        result = row_to_dict(cur)
    if not result:
        return jsonify({'error': 'Cliente não encontrado'}), 404
    return jsonify(result)


# ==================== API USUÁRIOS ====================

@app.route('/api/usuarios', methods=['GET'])
@login_required
def get_usuarios():
    if current_user.nivel != 'admin':
        return jsonify({'error': 'Acesso negado'}), 403
    with _db() as (conn, cur):
        cur.execute('SELECT id, nome, email, nivel, ativo, data_cadastro FROM usuarios ORDER BY nome')
        result = rows_to_dict(cur)
    return jsonify(result)


@app.route('/api/usuarios', methods=['POST'])
@login_required
def add_usuario():
    if current_user.nivel != 'admin':
        return jsonify({'error': 'Acesso negado'}), 403
    data = request.json
    try:
        with _db() as (conn, cur):
            cur.execute(
                'INSERT INTO usuarios (nome, email, senha_hash, nivel) VALUES (%s, %s, %s, %s)',
                (data['nome'], data['email'].lower(), generate_password_hash(data['senha']), data.get('nivel', 'operador'))
            )
        return jsonify({'success': True})
    except psycopg2.IntegrityError:
        return jsonify({'error': 'E-mail já cadastrado!'}), 400
    except Exception as e:
        app.logger.error('Erro em add_usuario: %s', e)
        return jsonify({'error': 'Erro interno. Tente novamente.'}), 500


@app.route('/api/usuarios/<int:id>', methods=['PUT'])
@login_required
def update_usuario(id):
    if current_user.nivel != 'admin':
        return jsonify({'error': 'Acesso negado'}), 403
    data = request.json
    try:
        with _db() as (conn, cur):
            if data.get('senha'):
                cur.execute(
                    'UPDATE usuarios SET nome=%s, email=%s, nivel=%s, ativo=%s, senha_hash=%s WHERE id=%s',
                    (data['nome'], data['email'].lower(), data['nivel'], data.get('ativo', True),
                     generate_password_hash(data['senha']), id)
                )
            else:
                cur.execute(
                    'UPDATE usuarios SET nome=%s, email=%s, nivel=%s, ativo=%s WHERE id=%s',
                    (data['nome'], data['email'].lower(), data['nivel'], data.get('ativo', True), id)
                )
        return jsonify({'success': True})
    except psycopg2.IntegrityError:
        return jsonify({'error': 'E-mail já cadastrado!'}), 400
    except Exception as e:
        app.logger.error('Erro em update_usuario: %s', e)
        return jsonify({'error': 'Erro interno. Tente novamente.'}), 500


@app.route('/api/usuarios/<int:id>', methods=['DELETE'])
@login_required
def delete_usuario(id):
    if current_user.nivel != 'admin':
        return jsonify({'error': 'Acesso negado'}), 403
    if id == current_user.id:
        return jsonify({'error': 'Não é possível excluir o próprio usuário'}), 400
    with _db() as (conn, cur):
        cur.execute('UPDATE usuarios SET ativo=FALSE WHERE id=%s', (id,))
    return jsonify({'success': True})


# ==================== PROXY FIPE (Parallelum) ====================
# BrasilAPI FIPE está fora do ar (403 no upstream); usando fipe.parallelum.com.br

_PARALLELUM = 'https://fipe.parallelum.com.br/api/v2'
_FIPE_TIPO  = {'carros': 'cars', 'motos': 'motorcycles', 'caminhoes': 'trucks'}

@app.route('/api/fipe/marcas/<tipo>')
@login_required
def fipe_marcas(tipo):
    try:
        t = _FIPE_TIPO.get(tipo, 'cars')
        r = requests.get(f'{_PARALLELUM}/{t}/brands', timeout=10)
        data = r.json()
        return jsonify([{'valor': str(m['code']), 'nome': m['name']} for m in data])
    except Exception as e:
        app.logger.error('Erro em fipe_marcas: %s', e)
        return jsonify({'error': 'Serviço FIPE indisponível. Tente novamente.'}), 503

@app.route('/api/fipe/modelos/<marca>')
@login_required
def fipe_modelos(marca):
    try:
        t = _FIPE_TIPO.get(request.args.get('tipo', 'carros'), 'cars')
        r = requests.get(f'{_PARALLELUM}/{t}/brands/{marca}/models', timeout=10)
        data = r.json()
        return jsonify([{'valor': str(m['code']), 'modelo': m['name']} for m in data])
    except Exception as e:
        app.logger.error('Erro em fipe_modelos: %s', e)
        return jsonify({'error': 'Serviço FIPE indisponível. Tente novamente.'}), 503

@app.route('/api/fipe/anos/<marca>/<modelo>')
@login_required
def fipe_anos(marca, modelo):
    try:
        t = _FIPE_TIPO.get(request.args.get('tipo', 'carros'), 'cars')
        r = requests.get(f'{_PARALLELUM}/{t}/brands/{marca}/models/{modelo}/years', timeout=10)
        data = r.json()
        return jsonify([{'valor': str(a['code']), 'nome': a['name']} for a in data])
    except Exception as e:
        app.logger.error('Erro em fipe_anos: %s', e)
        return jsonify({'error': 'Serviço FIPE indisponível. Tente novamente.'}), 503

@app.route('/api/fipe/preco/<marca>/<modelo>/<ano>')
@login_required
def fipe_preco(marca, modelo, ano):
    try:
        t = _FIPE_TIPO.get(request.args.get('tipo', 'carros'), 'cars')
        r = requests.get(f'{_PARALLELUM}/{t}/brands/{marca}/models/{modelo}/years/{ano}', timeout=10)
        d = r.json()
        return jsonify({
            'valor':        d.get('price', ''),
            'codigoFipe':   d.get('codeFipe', ''),
            'mesReferencia':d.get('referenceMonth', ''),
            'modelo':       d.get('model', ''),
        })
    except Exception as e:
        app.logger.error('Erro em fipe_preco: %s', e)
        return jsonify({'error': 'Serviço FIPE indisponível. Tente novamente.'}), 503


# ==================== CONTAS A RECEBER ====================

@app.route('/contas-receber')
@login_required
def contas_receber_page():
    return render_template('contas_receber.html')

# ==================== GERAÇÃO AUTOMÁTICA DE PERÍODOS RECORRENTES ====================

def gerar_periodos_pendentes():
    """Cria registros pendentes para contratos recorrentes com períodos já vencidos."""
    hoje = _date.today()
    freq_dias = {'semanal': 7, 'quinzenal': 15, 'mensal': 30}
    try:
        with _db() as (conn, cur):
            cur.execute('''
                SELECT l.id, l.data_inicio, l.data_fim, l.diaria, l.frequencia_cobranca
                FROM locacoes l
                WHERE l.status = 'ativa'
                  AND l.frequencia_cobranca IN ('semanal', 'quinzenal', 'mensal')
            ''')
            contratos = cur.fetchall()
            gerados = 0
            for loc_id, data_inicio, data_fim_loc, diaria, freq in contratos:
                if not data_inicio:
                    continue
                if isinstance(data_inicio, str):
                    data_inicio = _date.fromisoformat(data_inicio)
                if data_fim_loc and isinstance(data_fim_loc, str):
                    data_fim_loc = _date.fromisoformat(str(data_fim_loc))
                n_dias = freq_dias[freq]
                diaria_f = float(diaria or 0)
                periodo_ini = data_inicio
                while True:
                    periodo_fim = periodo_ini + timedelta(days=n_dias - 1)
                    if data_fim_loc:
                        if periodo_ini > data_fim_loc:
                            break
                        if periodo_fim > data_fim_loc:
                            periodo_fim = data_fim_loc
                    # Só gera períodos cujo fim já passou (período vencido)
                    if periodo_fim >= hoje:
                        break
                    # Verifica se já existe registro cobrindo este período
                    cur.execute('''
                        SELECT COUNT(*) FROM pagamentos_locacao
                        WHERE locacao_id=%s AND data_inicio <= %s AND data_fim >= %s
                    ''', (loc_id, periodo_fim, periodo_ini))
                    if cur.fetchone()[0] == 0:
                        dias = (periodo_fim - periodo_ini).days + 1
                        valor_prev = round(diaria_f * dias, 2)
                        cur.execute(
                            'SELECT COALESCE(MAX(semana_numero),0)+1 FROM pagamentos_locacao WHERE locacao_id=%s',
                            (loc_id,)
                        )
                        next_seq = cur.fetchone()[0]
                        cur.execute('''
                            INSERT INTO pagamentos_locacao
                                (locacao_id, semana_numero, data_inicio, data_fim,
                                 valor_previsto, valor_pago, desconto, status)
                            VALUES (%s, %s, %s, %s, %s, 0, 0, 'pendente')
                        ''', (loc_id, next_seq, periodo_ini, periodo_fim, valor_prev))
                        gerados += 1
                    periodo_ini = periodo_fim + timedelta(days=1)
    except Exception as e:
        print(f'[gerar_periodos_pendentes] {e}')


@app.route('/api/projecao-cobrancas')
@login_required
def projecao_cobrancas():
    """Retorna: vence_hoje, vence_semana, projecao_mes (pendentes + futuros projetados)."""
    hoje = _date.today()
    freq_dias = {'semanal': 7, 'quinzenal': 15, 'mensal': 30}
    mes_ini = hoje.replace(day=1)
    if mes_ini.month == 12:
        mes_fim = _date(mes_ini.year + 1, 1, 1) - timedelta(days=1)
    else:
        mes_fim = _date(mes_ini.year, mes_ini.month + 1, 1) - timedelta(days=1)
    semana_fim = hoje + timedelta(days=6)

    with _db() as (conn, cur):
        cur.execute('''
            SELECT pl.data_fim, pl.valor_previsto
            FROM pagamentos_locacao pl WHERE pl.status = 'pendente'
        ''')
        pendentes = [(r[0] if isinstance(r[0], _date) else (_date.fromisoformat(str(r[0])) if r[0] else None),
                      float(r[1] or 0)) for r in cur.fetchall()]

        vence_hoje   = sum(v for d, v in pendentes if d == hoje)
        vence_semana = sum(v for d, v in pendentes if d and hoje <= d <= semana_fim)
        projecao_mes = sum(v for d, v in pendentes if d and mes_ini <= d <= mes_fim)

        # Adiciona períodos futuros ainda não gerados
        cur.execute('''
            SELECT l.id, l.data_inicio, l.data_fim, l.diaria, l.frequencia_cobranca
            FROM locacoes l
            WHERE l.status = 'ativa' AND l.frequencia_cobranca IN ('semanal', 'quinzenal', 'mensal')
        ''')
        for loc_id, di, df, diaria, freq in cur.fetchall():
            if not di: continue
            if isinstance(di, str): di = _date.fromisoformat(di)
            if df and isinstance(df, str): df = _date.fromisoformat(str(df))
            n_dias = freq_dias[freq]
            diaria_f = float(diaria or 0)
            periodo_ini = di
            while True:
                periodo_fim = periodo_ini + timedelta(days=n_dias - 1)
                if df:
                    if periodo_ini > df: break
                    if periodo_fim > df: periodo_fim = df
                if periodo_ini > mes_fim: break
                # Período ainda não vencido e termina neste mês
                if periodo_fim >= hoje and mes_ini <= periodo_fim <= mes_fim:
                    cur.execute('''
                        SELECT COUNT(*) FROM pagamentos_locacao
                        WHERE locacao_id=%s AND data_inicio <= %s AND data_fim >= %s
                    ''', (loc_id, periodo_fim, periodo_ini))
                    if cur.fetchone()[0] == 0:
                        dias = (periodo_fim - periodo_ini).days + 1
                        projecao_mes += round(diaria_f * dias, 2)
                periodo_ini = periodo_fim + timedelta(days=1)

    return jsonify({
        'vence_hoje':   round(vence_hoje, 2),
        'vence_semana': round(vence_semana, 2),
        'projecao_mes': round(projecao_mes, 2),
    })


@app.route('/api/alertas-cobranca')
@login_required
def alertas_cobranca():
    """Retorna cobranças recorrentes com período vencido e sem pagamento."""
    with _db() as (conn, cur):
        cur.execute('''
            SELECT pl.id, pl.data_fim, pl.valor_previsto,
                   c.nome AS cliente, v.placa, v.marca, v.modelo,
                   l.frequencia_cobranca
            FROM pagamentos_locacao pl
            JOIN locacoes l ON l.id = pl.locacao_id
            JOIN veiculos v ON v.id = l.veiculo_id
            JOIN clientes c ON c.id = l.cliente_id
            WHERE pl.status = 'pendente'
              AND pl.data_fim < CURRENT_DATE
              AND l.frequencia_cobranca IN ('semanal','quinzenal','mensal')
            ORDER BY pl.data_fim ASC
            LIMIT 20
        ''')
        result = rows_to_dict(cur)
    return jsonify(result)


@app.route('/api/contas-receber')
@login_required
def get_contas_receber():
    gerar_periodos_pendentes()
    with _db() as (conn, cur):
        cur.execute('''
        SELECT tipo, id, cliente, cliente_cpf, cliente_telefone, cliente_email,
               descricao, valor, desconto, justificativa_desconto,
               data_recebimento, data_vencimento,
               status, data_cadastro, cliente_id, locacao_id,
               data_inicio_period, data_fim_period, placa, observacao
        FROM (
            SELECT
                'contrato'::text AS tipo, pl.id,
                c.nome AS cliente, COALESCE(c.cpf,'') AS cliente_cpf,
                COALESCE(c.telefone,'') AS cliente_telefone,
                COALESCE(c.email,'') AS cliente_email,
                (v.placa || ' — ' || v.marca || ' ' || v.modelo) AS descricao,
                CASE WHEN pl.status = 'pago' THEN COALESCE(pl.valor_pago,0)
                     ELSE COALESCE(pl.valor_previsto,0) END AS valor,
                COALESCE(pl.desconto,0) AS desconto,
                pl.justificativa_desconto,
                pl.data_pagamento AS data_recebimento,
                pl.data_fim AS data_vencimento,
                pl.status, pl.data_cadastro,
                l.cliente_id AS cliente_id,
                pl.locacao_id AS locacao_id,
                pl.data_inicio AS data_inicio_period,
                pl.data_fim AS data_fim_period,
                v.placa AS placa,
                pl.observacao AS observacao
            FROM pagamentos_locacao pl
            JOIN locacoes l ON l.id = pl.locacao_id
            JOIN veiculos v ON v.id = l.veiculo_id
            JOIN clientes c ON c.id = l.cliente_id
            WHERE pl.status != 'cancelado'
            UNION ALL
            SELECT
                'multa'::text AS tipo, m.id,
                COALESCE(c.nome,'Sem motorista') AS cliente,
                COALESCE(c.cpf,'') AS cliente_cpf,
                COALESCE(c.telefone,'') AS cliente_telefone,
                COALESCE(c.email,'') AS cliente_email,
                ('Multa: ' || m.descricao) AS descricao,
                m.valor, COALESCE(m.desconto,0) AS desconto,
                m.justificativa_desconto,
                m.data_pagamento AS data_recebimento,
                m.data_infracao AS data_vencimento,
                m.status, m.data_infracao::timestamp AS data_cadastro,
                m.motorista_id AS cliente_id,
                NULL::int AS locacao_id,
                NULL::date AS data_inicio_period,
                NULL::date AS data_fim_period,
                ve.placa AS placa,
                m.observacao AS observacao
            FROM multas m
            LEFT JOIN clientes c ON m.motorista_id = c.id
            LEFT JOIN veiculos ve ON m.veiculo_id = ve.id
            UNION ALL
            SELECT
                'avulsa'::text AS tipo, ca.id,
                COALESCE(c.nome,'Sem cliente') AS cliente,
                COALESCE(c.cpf,'') AS cliente_cpf,
                COALESCE(c.telefone,'') AS cliente_telefone,
                COALESCE(c.email,'') AS cliente_email,
                ca.descricao, ca.valor,
                COALESCE(ca.desconto,0) AS desconto,
                ca.justificativa_desconto,
                ca.data_recebimento, ca.data_vencimento,
                ca.status, ca.data_cadastro,
                ca.cliente_id AS cliente_id,
                NULL::int AS locacao_id,
                NULL::date AS data_inicio_period,
                NULL::date AS data_fim_period,
                NULL::text AS placa,
                ca.observacoes AS observacao
            FROM cobrancas_avulsas ca
            LEFT JOIN clientes c ON ca.cliente_id = c.id
        ) sub
        ORDER BY data_cadastro DESC NULLS LAST
        ''')
        result = rows_to_dict(cur)
    return jsonify(result)

@app.route('/api/cobrancas-avulsas', methods=['POST'])
@login_required
def create_cobranca_avulsa():
    data = request.json
    with _db() as (conn, cur):
        cur.execute('''
            INSERT INTO cobrancas_avulsas (cliente_id, descricao, valor, data_vencimento, status, observacoes)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        ''', (data.get('cliente_id'), data.get('descricao'), float(data.get('valor') or 0),
              data.get('data_vencimento'), data.get('status', 'pendente'), data.get('observacoes')))
        new_id = cur.fetchone()[0]
    return jsonify({'success': True, 'id': new_id})

@app.route('/api/cobrancas-avulsas/<int:id>', methods=['PUT'])
@login_required
def update_cobranca_avulsa(id):
    data = request.json
    with _db() as (conn, cur):
        cur.execute('''
            UPDATE cobrancas_avulsas
            SET cliente_id=%s, descricao=%s, valor=%s, data_vencimento=%s,
                status=%s, data_recebimento=%s, observacoes=%s
            WHERE id=%s
        ''', (data.get('cliente_id'), data.get('descricao'), float(data.get('valor') or 0),
              data.get('data_vencimento'), data.get('status'), data.get('data_recebimento'),
              data.get('observacoes'), id))
    return jsonify({'success': True})

@app.route('/api/cobrancas-avulsas/<int:id>', methods=['DELETE'])
@login_required
def delete_cobranca_avulsa(id):
    with _db() as (conn, cur):
        cur.execute('DELETE FROM cobrancas_avulsas WHERE id=%s', (id,))
    return jsonify({'success': True})

@app.route('/api/cobrar-asaas-avulsa', methods=['POST'])
@login_required
def cobrar_asaas_avulsa():
    data        = request.json
    cliente_id  = data.get('cliente_id')
    descricao   = (data.get('descricao') or '').strip()
    valor       = float(data.get('valor') or 0)
    billing     = data.get('billing_type', 'PIX')
    parcelas    = data.get('parcelas')   # [{data_vencimento, valor}, ...]
    observacoes = data.get('observacoes')

    if not descricao or valor <= 0:
        return jsonify({'error': 'Descrição e valor são obrigatórios'}), 400
    if not cliente_id:
        return jsonify({'error': 'Selecione um cliente para cobrar via Asaas'}), 400

    with _db() as (conn, cur):
        cur.execute('SELECT nome, cpf, telefone, email FROM clientes WHERE id=%s', (cliente_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Cliente não encontrado'}), 404
        cli_nome, cli_cpf, cli_tel, cli_email = row

        if not cli_cpf:
            return jsonify({'error': 'Cliente sem CPF cadastrado — necessário para Asaas'}), 400

        customer_id, err = _asaas_get_or_create_customer(cli_cpf, cli_nome, cli_email or '', cli_tel or '')
        if err:
            return jsonify({'error': f'Asaas: {err}'}), 500

        if parcelas and len(parcelas) > 1:
            n = len(parcelas)
            itens = [{'due_date': p.get('data_vencimento') or str(_date.today()),
                      'valor': round(float(p.get('valor') or 0), 2),
                      'parcela_num': i + 1, 'total': n,
                      'descricao': f'{descricao} — Parcela {i+1}/{n}'}
                     for i, p in enumerate(parcelas)]
        else:
            due = (parcelas[0].get('data_vencimento') if parcelas else None) or str(_date.today())
            itens = [{'due_date': due, 'valor': valor, 'parcela_num': 1, 'total': 1, 'descricao': descricao}]

        resultados = []
        for idx, item in enumerate(itens):
            charge, sc = _asaas_req('POST', '/payments', {
                'customer':    customer_id,
                'billingType': billing,
                'dueDate':     item['due_date'],
                'value':       item['valor'],
                'description': item['descricao'],
            })
            if sc not in (200, 201):
                errs = charge.get('errors', [{}])
                raise Exception(f"Parcela {item['parcela_num']}: {errs[0].get('description','Erro Asaas')}")

            asaas_id   = charge.get('id')
            asaas_link = charge.get('invoiceUrl', '')

            cur.execute('''
                INSERT INTO cobrancas_avulsas
                    (cliente_id, descricao, valor, data_vencimento, status, observacoes,
                     asaas_id, asaas_link, asaas_status)
                VALUES (%s,%s,%s,%s,'pendente',%s,%s,%s,'PENDING') RETURNING id
            ''', (cliente_id, item['descricao'], item['valor'], item['due_date'],
                  observacoes if idx == 0 else None, asaas_id, asaas_link))
            new_id = cur.fetchone()[0]

            r = {'id': new_id, 'asaas_id': asaas_id, 'invoice_url': asaas_link,
                 'bank_slip_url': charge.get('bankSlipUrl', ''),
                 'parcela_num': item['parcela_num'], 'total': item['total'], 'valor': item['valor']}
            if billing == 'PIX':
                pix, _ = _asaas_req('GET', f'/payments/{asaas_id}/pixQrCode')
                r['pix_qrcode']  = pix.get('encodedImage', '')
                r['pix_payload'] = pix.get('payload', '')
            resultados.append(r)

    return jsonify({'parcelas': resultados, 'cliente_nome': cli_nome,
                    'cliente_tel': cli_tel or '', 'cliente_email': cli_email or ''})


@app.route('/api/cobrar-asaas-contrato-pendente', methods=['POST'])
@login_required
def cobrar_asaas_contrato_pendente():
    """Cobra via Asaas um pagamento_locacao já existente (pendente)."""
    data       = request.json
    pag_id     = data.get('pagamento_id')
    billing    = data.get('billing_type', 'PIX')
    parcelas   = data.get('parcelas')  # [{data_vencimento, valor}]

    with _db() as (conn, cur):
        cur.execute('''
            SELECT pl.id, pl.locacao_id, pl.data_inicio, pl.data_fim,
                   pl.valor_previsto, pl.valor_pago, pl.status,
                   c.nome, c.cpf, c.telefone, c.email,
                   v.placa, v.marca, v.modelo
            FROM pagamentos_locacao pl
            JOIN locacoes l ON l.id = pl.locacao_id
            JOIN clientes c ON c.id  = l.cliente_id
            JOIN veiculos v ON v.id  = l.veiculo_id
            WHERE pl.id = %s
        ''', (pag_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Pagamento não encontrado'}), 404

        (pid, loc_id, d_ini, d_fim, val_prev, val_pago, status,
         cli_nome, cli_cpf, cli_tel, cli_email,
         placa, marca, modelo) = row

        if not cli_cpf:
            return jsonify({'error': 'Cliente sem CPF — necessário para Asaas'}), 400

        customer_id, err = _asaas_get_or_create_customer(
            cli_cpf, cli_nome, cli_email or '', cli_tel or '')
        if err:
            return jsonify({'error': f'Asaas: {err}'}), 500

        valor_total = float(val_prev or 0)
        desc_base   = f'Locação {placa} {marca} {modelo} ({d_ini} a {d_fim})'

        if parcelas and len(parcelas) > 1:
            n    = len(parcelas)
            itens = [{'due_date': p.get('data_vencimento') or str(_date.today()),
                      'valor': round(float(p.get('valor') or 0), 2),
                      'parcela_num': i + 1, 'total': n,
                      'descricao': f'{desc_base} — Parcela {i+1}/{n}'}
                     for i, p in enumerate(parcelas)]
        else:
            due = (parcelas[0].get('data_vencimento') if parcelas else None) or str(d_fim or _date.today())
            itens = [{'due_date': due, 'valor': valor_total,
                      'parcela_num': 1, 'total': 1, 'descricao': desc_base}]

        resultados = []
        first = True
        for item in itens:
            charge, sc = _asaas_req('POST', '/payments', {
                'customer':    customer_id,
                'billingType': billing,
                'dueDate':     item['due_date'],
                'value':       item['valor'],
                'description': item['descricao'],
            })
            if sc not in (200, 201):
                errs = charge.get('errors', [{}])
                raise Exception(errs[0].get('description', 'Erro Asaas'))

            asaas_id   = charge.get('id')
            asaas_link = charge.get('invoiceUrl', '')

            if first:
                cur.execute('''UPDATE pagamentos_locacao
                               SET asaas_id=%s, asaas_link=%s, asaas_status='PENDING',
                                   valor_pago=%s
                               WHERE id=%s''',
                            (asaas_id, asaas_link, item['valor'], pid))
                first = False
            else:
                cur.execute('''SELECT COALESCE(MAX(semana_numero),0)+1
                               FROM pagamentos_locacao WHERE locacao_id=%s''', (loc_id,))
                seq = cur.fetchone()[0]
                cur.execute('''
                    INSERT INTO pagamentos_locacao
                        (locacao_id, semana_numero, data_inicio, data_fim,
                         valor_previsto, valor_pago, status,
                         asaas_id, asaas_link, asaas_status,
                         parcela_num, total_parcelas)
                    VALUES (%s,%s,%s,%s,%s,%s,'pendente',%s,%s,'PENDING',%s,%s)
                ''', (loc_id, seq, d_ini, d_fim,
                      item['valor'], item['valor'],
                      asaas_id, asaas_link,
                      item['parcela_num'], item['total']))

            r = {'asaas_id': asaas_id, 'invoice_url': asaas_link,
                 'bank_slip_url': charge.get('bankSlipUrl', ''),
                 'parcela_num': item['parcela_num'], 'total': item['total'],
                 'valor': item['valor']}
            if billing == 'PIX':
                pix, _ = _asaas_req('GET', f'/payments/{asaas_id}/pixQrCode')
                r['pix_qrcode']  = pix.get('encodedImage', '')
                r['pix_payload'] = pix.get('payload', '')
            resultados.append(r)

    return jsonify({'parcelas': resultados, 'cliente_nome': cli_nome,
                    'cliente_tel': cli_tel or '', 'cliente_email': cli_email or ''})


@app.route('/api/contas-receber/observacao', methods=['PUT'])
@login_required
def salvar_observacao_cr():
    data = request.json
    tipo = data.get('tipo')
    id_  = data.get('id')
    obs  = data.get('observacao', '').strip() or None
    with _db() as (conn, cur):
        if tipo == 'contrato':
            cur.execute('UPDATE pagamentos_locacao SET observacao=%s WHERE id=%s', (obs, id_))
        elif tipo == 'multa':
            cur.execute('UPDATE multas SET observacao=%s WHERE id=%s', (obs, id_))
        elif tipo == 'avulsa':
            cur.execute('UPDATE cobrancas_avulsas SET observacoes=%s WHERE id=%s', (obs, id_))
        else:
            return jsonify({'error': 'tipo inválido'}), 400
    return jsonify({'success': True})


@app.route('/api/contas-receber/baixa', methods=['PUT'])
@login_required
def baixa_contas_receber():
    data       = request.json
    tipo       = data.get('tipo')
    rid        = data.get('id')
    data_rec   = data.get('data_recebimento') or str(_date.today())
    desconto   = float(data.get('desconto') or 0)
    justific   = data.get('justificativa_desconto') or ''
    with _db() as (conn, cur):
        if tipo == 'contrato':
            cur.execute('SELECT valor_previsto FROM pagamentos_locacao WHERE id=%s', (rid,))
            row = cur.fetchone()
            val_prev   = float(row[0] or 0) if row else 0
            valor_pago = max(0, round(val_prev - desconto, 2))
            cur.execute('''UPDATE pagamentos_locacao
                           SET status='pago', data_pagamento=%s,
                               valor_pago=%s, desconto=%s, justificativa_desconto=%s
                           WHERE id=%s''',
                        (data_rec, valor_pago, desconto, justific, rid))
        elif tipo == 'multa':
            cur.execute('''UPDATE multas SET status='pago', data_pagamento=%s,
                               desconto=%s, justificativa_desconto=%s WHERE id=%s''',
                        (data_rec, desconto, justific, rid))
        elif tipo == 'avulsa':
            cur.execute('''UPDATE cobrancas_avulsas SET status='recebido', data_recebimento=%s,
                               desconto=%s, justificativa_desconto=%s WHERE id=%s''',
                        (data_rec, desconto, justific, rid))
    return jsonify({'success': True})

# Roda migrations no cold start do Vercel (e também localmente via __main__)
try:
    init_db()
except Exception as _e:
    print(f'[init_db] {_e}')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
