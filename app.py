from flask import Flask, render_template, request, jsonify, redirect, url_for, send_from_directory, Response
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import psycopg2
import psycopg2.errorcodes
import cloudinary
import cloudinary.uploader
import os
import base64
import re
import io
from datetime import datetime
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
import pdfplumber

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-troque-em-producao')
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


def rows_to_dict(cursor):
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def row_to_dict(cursor):
    cols = [d[0] for d in cursor.description]
    row = cursor.fetchone()
    return dict(zip(cols, row)) if row else None


def init_db():
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
    cur.execute("ALTER TABLE multas ADD COLUMN IF NOT EXISTS numero_auto TEXT")

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

    conn.commit()
    cur.close()
    conn.close()
    print('Banco de dados inicializado com sucesso!')

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
            conn = get_conn()
            cur = conn.cursor()
            cur.execute(
                'SELECT id, nome, email, nivel, senha_hash FROM usuarios WHERE email = %s AND ativo = TRUE',
                (email,)
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
        except Exception as e:
            return render_template('login.html', erro=f'Erro de conexão com o banco: {e}')

        if row and check_password_hash(row[4], senha):
            user = User(row[0], row[1], row[2], row[3])
            login_user(user, remember=lembrar)
            return redirect(request.args.get('next') or url_for('index'))

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
        conn = get_conn()
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM usuarios')
        total = cur.fetchone()[0]
        cur.close()
        conn.close()
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
            conn = get_conn()
            cur = conn.cursor()
            cur.execute(
                'INSERT INTO usuarios (nome, email, senha_hash, nivel) VALUES (%s, %s, %s, %s)',
                (nome, email, generate_password_hash(senha), 'admin')
            )
            conn.commit()
            cur.close()
            conn.close()
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
        return jsonify({'error': str(e)}), 500


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

        conn = get_conn()
        cur = conn.cursor()
        cur.execute('UPDATE veiculos SET foto = %s WHERE id = %s', (foto_url, id))
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({'success': True, 'foto_url': foto_url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/veiculos/<int:id>/foto', methods=['DELETE'])
@login_required
def delete_foto_veiculo(id):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute('UPDATE veiculos SET foto = NULL WHERE id = %s', (id,))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== API CLIENTES ====================

@app.route('/api/clientes', methods=['GET'])
@login_required
def get_clientes():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT * FROM clientes ORDER BY nome')
    result = rows_to_dict(cur)
    cur.close()
    conn.close()
    return jsonify(result)


@app.route('/api/clientes', methods=['POST'])
@login_required
def add_cliente():
    data = request.json
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO clientes (nome, cpf, cnh, telefone, email, endereco, cidade, estado, status, observacoes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (data['nome'], data.get('cpf'), data.get('cnh'), data.get('telefone'),
              data.get('email'), data.get('endereco'), data.get('cidade'),
              data.get('estado'), data.get('status', 'ativo'), data.get('observacoes')))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True})
    except psycopg2.IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'CPF já cadastrado!'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/clientes/<int:id>', methods=['PUT'])
@login_required
def update_cliente(id):
    data = request.json
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
        UPDATE clientes SET nome=%s, cpf=%s, cnh=%s, telefone=%s, email=%s,
        endereco=%s, cidade=%s, estado=%s, status=%s, observacoes=%s WHERE id=%s
    ''', (data['nome'], data.get('cpf'), data.get('cnh'), data.get('telefone'),
          data.get('email'), data.get('endereco'), data.get('cidade'),
          data.get('estado'), data.get('status'), data.get('observacoes'), id))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/clientes/<int:id>', methods=['DELETE'])
@login_required
def delete_cliente(id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('DELETE FROM clientes WHERE id=%s', (id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

# ==================== API VEÍCULOS ====================

@app.route('/api/veiculos', methods=['GET'])
@login_required
def get_veiculos():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT * FROM veiculos ORDER BY marca, modelo')
    result = rows_to_dict(cur)
    cur.close()
    conn.close()
    return jsonify(result)


@app.route('/api/veiculos', methods=['POST'])
@login_required
def add_veiculo():
    data = request.json
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO veiculos (placa, marca, modelo, ano, cor, categoria, diaria, km_atual, status, combustivel, observacoes, renavam, chassi, ano_fabricacao, potencia, versao)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        ''', (data.get('placa'), data.get('marca'), data.get('modelo'),
              data.get('ano') or None, data.get('cor') or None,
              data.get('categoria') or None, data.get('diaria') or None,
              data.get('km_atual') or 0, data.get('status', 'disponivel'),
              data.get('combustivel') or None, data.get('observacoes') or None,
              data.get('renavam') or None, data.get('chassi') or None,
              data.get('ano_fabricacao') or None,
              data.get('potencia') or None, data.get('versao') or None))
        veiculo_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True, 'id': veiculo_id})
    except psycopg2.IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Placa já cadastrada!'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/veiculos/<int:id>', methods=['PUT'])
@login_required
def update_veiculo(id):
    data = request.json
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute('''
            UPDATE veiculos SET placa=%s, marca=%s, modelo=%s, ano=%s, cor=%s, categoria=%s,
            diaria=%s, km_atual=%s, status=%s, combustivel=%s, observacoes=%s, renavam=%s,
            chassi=%s, ano_fabricacao=%s, potencia=%s, versao=%s WHERE id=%s
        ''', (data.get('placa'), data.get('marca'), data.get('modelo'),
              data.get('ano') or None, data.get('cor'), data.get('categoria'),
              data.get('diaria') or None, data.get('km_atual') or 0,
              data.get('status', 'disponivel'), data.get('combustivel'),
              data.get('observacoes'), data.get('renavam') or None,
              data.get('chassi') or None,
              data.get('ano_fabricacao') or None,
              data.get('potencia') or None, data.get('versao') or None, id))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True})
    except psycopg2.IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'Placa já cadastrada por outro veículo!'}), 400
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'error': str(e)}), 500


@app.route('/api/veiculos/<int:id>', methods=['DELETE'])
@login_required
def delete_veiculo(id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('DELETE FROM veiculos WHERE id=%s', (id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

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
        conn = get_conn()
        cur = conn.cursor()
        cur.execute('UPDATE veiculos SET crlv_url = %s WHERE id = %s', (crlv_url, id))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True, 'crlv_url': crlv_url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/veiculos/<int:id>/crlv/download')
@login_required
def download_crlv_veiculo(id):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute('SELECT crlv_url, placa FROM veiculos WHERE id = %s', (id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
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
        return jsonify({'error': str(e)}), 500


@app.route('/api/veiculos/<int:id>/crlv', methods=['DELETE'])
@login_required
def delete_crlv_veiculo(id):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute('UPDATE veiculos SET crlv_url = NULL WHERE id = %s', (id,))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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
        return jsonify({'error': f'Erro ao processar PDF: {str(e)}'}), 500


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
    MARCAS_ABREV = {'VW', 'GM', 'BMW', 'KIA', 'JAC', 'BYD', 'GWM', 'MG', 'GAC', 'JMC'}
    val_mmv = proxima_valor(r'MARCA\s*/?\s*MODELO')
    if val_mmv and '/' in val_mmv:
        idx = val_mmv.index('/')
        marca_raw = val_mmv[:idx].strip().upper()
        dados['marca'] = marca_raw if marca_raw in MARCAS_ABREV else marca_raw.title()
        resto = val_mmv[idx + 1:].strip().split()

        # VERSÃO: primeiro token que é estritamente um número de versão (ex: "1.0", "1.6i")
        versao_token = next(
            (t for t in resto if re.match(r'^\d+[.,]\d+\w{0,2}$', t)),
            None
        )
        if versao_token:
            vi = resto.index(versao_token)
            dados['modelo'] = ' '.join(resto[:vi]).title() if vi > 0 else (resto[0].title() if len(resto) > 1 else '')
            dados['versao'] = versao_token
        else:
            dados['modelo'] = ' '.join(resto).title()

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

@app.route('/api/manutencoes', methods=['GET'])
@login_required
def get_manutencoes():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
        SELECT m.*, v.placa, v.marca, v.modelo
        FROM manutencoes m
        LEFT JOIN veiculos v ON m.veiculo_id = v.id
        ORDER BY m.data_manutencao DESC
    ''')
    result = rows_to_dict(cur)
    cur.close()
    conn.close()
    return jsonify(result)


@app.route('/api/manutencoes/veiculo/<int:veiculo_id>')
@login_required
def get_manutencoes_veiculo(veiculo_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT * FROM manutencoes WHERE veiculo_id = %s ORDER BY data_manutencao DESC', (veiculo_id,))
    result = rows_to_dict(cur)
    cur.close()
    conn.close()
    return jsonify(result)


@app.route('/api/manutencoes', methods=['POST'])
@login_required
def add_manutencao():
    data = request.json
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO manutencoes (veiculo_id, tipo, descricao, data_manutencao, km_manutencao,
        custo, oficina, proxima_manutencao_km, proxima_manutencao_data, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ''', (data['veiculo_id'], data['tipo'], data.get('descricao'), data.get('data_manutencao'),
          data.get('km_manutencao'), data.get('custo'), data.get('oficina'),
          data.get('proxima_manutencao_km'), data.get('proxima_manutencao_data'),
          data.get('status', 'concluida')))

    if data.get('km_manutencao'):
        cur.execute('UPDATE veiculos SET km_atual = %s WHERE id = %s',
                    (data['km_manutencao'], data['veiculo_id']))

    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/manutencoes/<int:id>', methods=['PUT'])
@login_required
def update_manutencao(id):
    data = request.json
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
        UPDATE manutencoes SET veiculo_id=%s, tipo=%s, descricao=%s, data_manutencao=%s,
        km_manutencao=%s, custo=%s, oficina=%s, proxima_manutencao_km=%s,
        proxima_manutencao_data=%s, status=%s WHERE id=%s
    ''', (data.get('veiculo_id'), data.get('tipo'), data.get('descricao'),
          data.get('data_manutencao'), data.get('km_manutencao'), data.get('custo'),
          data.get('oficina'), data.get('proxima_manutencao_km'),
          data.get('proxima_manutencao_data'), data.get('status'), id))

    if data.get('km_manutencao'):
        cur.execute('UPDATE veiculos SET km_atual = %s WHERE id = %s',
                    (data['km_manutencao'], data.get('veiculo_id')))

    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/manutencoes/<int:id>', methods=['DELETE'])
@login_required
def delete_manutencao(id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('DELETE FROM manutencoes WHERE id=%s', (id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

# ==================== API LOCAÇÕES ====================

@app.route('/api/locacoes', methods=['GET'])
@login_required
def get_locacoes():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
        SELECT l.*, v.placa, v.marca, v.modelo, c.nome as nome_cliente
        FROM locacoes l
        LEFT JOIN veiculos v ON l.veiculo_id = v.id
        LEFT JOIN clientes c ON l.cliente_id = c.id
        ORDER BY l.data_inicio DESC
    ''')
    result = rows_to_dict(cur)
    cur.close()
    conn.close()
    return jsonify(result)


@app.route('/api/locacoes/<int:id>', methods=['GET'])
@login_required
def get_locacao(id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
        SELECT l.*, v.placa, v.marca, v.modelo, v.cor, v.ano, v.combustivel,
               c.nome as nome_cliente, c.cpf, c.cnh, c.telefone, c.endereco, c.cidade, c.estado
        FROM locacoes l
        LEFT JOIN veiculos v ON l.veiculo_id = v.id
        LEFT JOIN clientes c ON l.cliente_id = c.id
        WHERE l.id = %s
    ''', (id,))
    result = row_to_dict(cur)
    cur.close()
    conn.close()
    if not result:
        return jsonify({'error': 'Locação não encontrada'}), 404
    return jsonify(result)


@app.route('/api/locacoes', methods=['POST'])
@login_required
def add_locacao():
    data = request.json
    import json as _json
    dias = (datetime.strptime(data['data_fim'], '%Y-%m-%d') -
            datetime.strptime(data['data_inicio'], '%Y-%m-%d')).days + 1
    total = dias * float(data.get('diaria', 0))

    fotos_saida = data.get('fotos_saida')
    if fotos_saida and isinstance(fotos_saida, list):
        fotos_saida = _json.dumps(fotos_saida)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO locacoes (veiculo_id, cliente_id, data_inicio, data_fim, diaria, total, km_saida, status, checklist, fotos_saida, observacoes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ''', (data['veiculo_id'], data['cliente_id'], data['data_inicio'], data['data_fim'],
          data['diaria'], total, data.get('km_saida'), data.get('status', 'ativa'),
          data.get('checklist'), fotos_saida, data.get('observacoes')))
    cur.execute("UPDATE veiculos SET status = 'locado' WHERE id = %s", (data['veiculo_id'],))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/locacoes/<int:id>', methods=['PUT'])
@login_required
def update_locacao(id):
    data = request.json
    conn = get_conn()
    cur = conn.cursor()

    cur.execute('SELECT veiculo_id, status FROM locacoes WHERE id = %s', (id,))
    locacao = cur.fetchone()
    if not locacao:
        cur.close()
        conn.close()
        return jsonify({'error': 'Locação não encontrada'}), 404

    veiculo_antigo, status_antigo = locacao

    import json as _json
    fotos_saida = data.get('fotos_saida')
    if fotos_saida and isinstance(fotos_saida, list):
        fotos_saida = _json.dumps(fotos_saida)

    cur.execute('''
        UPDATE locacoes SET veiculo_id=%s, cliente_id=%s, data_inicio=%s, data_fim=%s,
        diaria=%s, total=%s, km_saida=%s, checklist=%s, fotos_saida=%s, observacoes=%s WHERE id=%s
    ''', (data.get('veiculo_id'), data.get('cliente_id'), data.get('data_inicio'),
          data.get('data_fim'), data.get('diaria'), data.get('total'),
          data.get('km_saida'), data.get('checklist'),
          fotos_saida, data.get('observacoes'), id))

    veiculo_novo = data.get('veiculo_id')
    if veiculo_antigo != veiculo_novo and status_antigo == 'ativa':
        cur.execute("UPDATE veiculos SET status = 'disponivel' WHERE id = %s", (veiculo_antigo,))
        cur.execute("UPDATE veiculos SET status = 'locado' WHERE id = %s", (veiculo_novo,))

    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/locacoes/<int:id>/devolver', methods=['PUT'])
@login_required
def devolver_locacao(id):
    data = request.json
    conn = get_conn()
    cur = conn.cursor()

    cur.execute('SELECT veiculo_id FROM locacoes WHERE id = %s', (id,))
    locacao = cur.fetchone()

    if locacao:
        import json as _json
        fotos_retorno = data.get('fotos_retorno')
        if fotos_retorno and isinstance(fotos_retorno, list):
            fotos_retorno = _json.dumps(fotos_retorno)
        cur.execute(
            "UPDATE locacoes SET data_fim=%s, data_devolucao_real=%s, km_retorno=%s, fotos_retorno=%s, status='finalizada' WHERE id=%s",
            (data.get('data_fim'), data.get('data_devolucao_real'), data.get('km_retorno'), fotos_retorno, id)
        )
        if data.get('km_retorno'):
            cur.execute(
                "UPDATE veiculos SET km_atual=%s, status='disponivel' WHERE id=%s",
                (data['km_retorno'], locacao[0])
            )

    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

# ==================== API ABASTECIMENTOS ====================

@app.route('/api/abastecimentos', methods=['GET'])
@login_required
def get_abastecimentos():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
        SELECT a.*, v.placa, v.marca, v.modelo
        FROM abastecimentos a
        LEFT JOIN veiculos v ON a.veiculo_id = v.id
        ORDER BY a.data_abastecimento DESC
    ''')
    result = rows_to_dict(cur)
    cur.close()
    conn.close()
    return jsonify(result)


@app.route('/api/abastecimentos', methods=['POST'])
@login_required
def add_abastecimento():
    data = request.json
    total = float(data.get('litros', 0)) * float(data.get('valor_litro', 0))
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO abastecimentos (veiculo_id, data_abastecimento, litros, valor_litro, total, km_abastecimento)
        VALUES (%s, %s, %s, %s, %s, %s)
    ''', (data['veiculo_id'], data.get('data_abastecimento'), data.get('litros'),
          data.get('valor_litro'), total, data.get('km_abastecimento')))

    if data.get('km_abastecimento'):
        cur.execute('UPDATE veiculos SET km_atual = %s WHERE id = %s',
                    (data['km_abastecimento'], data['veiculo_id']))

    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/abastecimentos/<int:id>', methods=['DELETE'])
@login_required
def delete_abastecimento(id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('DELETE FROM abastecimentos WHERE id=%s', (id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

# ==================== API MULTAS ====================

@app.route('/api/multas', methods=['GET'])
@login_required
def get_multas():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
        SELECT m.*, v.placa, v.marca, v.modelo, c.nome as nome_motorista
        FROM multas m
        LEFT JOIN veiculos v ON m.veiculo_id = v.id
        LEFT JOIN clientes c ON m.motorista_id = c.id
        ORDER BY m.data_infracao DESC
    ''')
    result = rows_to_dict(cur)
    cur.close()
    conn.close()
    return jsonify(result)


@app.route('/api/multas', methods=['POST'])
@login_required
def add_multa():
    data = request.json
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO multas (veiculo_id, motorista_id, data_infracao, descricao, valor, local_infracao, pontos, status, observacoes, numero_auto)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ''', (data.get('veiculo_id'), data.get('motorista_id'), data.get('data_infracao'),
          data.get('descricao'), data.get('valor'), data.get('local_infracao'),
          data.get('pontos'), data.get('status', 'pendente'), data.get('observacoes'),
          data.get('numero_auto')))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/multas/<int:id>', methods=['PUT'])
@login_required
def update_multa(id):
    data = request.json
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
        UPDATE multas SET veiculo_id=%s, motorista_id=%s, data_infracao=%s, descricao=%s,
        valor=%s, local_infracao=%s, pontos=%s, status=%s, observacoes=%s, numero_auto=%s WHERE id=%s
    ''', (data.get('veiculo_id'), data.get('motorista_id'), data.get('data_infracao'),
          data.get('descricao'), data.get('valor'), data.get('local_infracao'),
          data.get('pontos'), data.get('status'), data.get('observacoes'),
          data.get('numero_auto'), id))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/multas/<int:id>', methods=['DELETE'])
@login_required
def delete_multa(id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('DELETE FROM multas WHERE id=%s', (id,))
    conn.commit()
    cur.close()
    conn.close()
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
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== API FORNECEDORES ====================

@app.route('/api/fornecedores', methods=['GET'])
@login_required
def get_fornecedores():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
        SELECT f.*, t.nome as tipo_nome
        FROM fornecedores f
        LEFT JOIN tipos_fornecedor t ON f.tipo_id = t.id
        ORDER BY f.nome
    ''')
    result = rows_to_dict(cur)
    cur.close()
    conn.close()
    return jsonify(result)


@app.route('/api/fornecedores', methods=['POST'])
@login_required
def add_fornecedor():
    data = request.json
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO fornecedores (nome, cnpj, cpf, telefone, email, endereco, cidade, estado, tipo_id, responsavel, status, observacoes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ''', (data['nome'], data.get('cnpj'), data.get('cpf'), data.get('telefone'),
          data.get('email'), data.get('endereco'), data.get('cidade'),
          data.get('estado'), data.get('tipo_id'), data.get('responsavel'),
          data.get('status', 'ativo'), data.get('observacoes')))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/fornecedores/<int:id>', methods=['PUT'])
@login_required
def update_fornecedor(id):
    data = request.json
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
        UPDATE fornecedores SET nome=%s, cnpj=%s, cpf=%s, telefone=%s, email=%s, endereco=%s,
        cidade=%s, estado=%s, tipo_id=%s, responsavel=%s, status=%s, observacoes=%s WHERE id=%s
    ''', (data['nome'], data.get('cnpj'), data.get('cpf'), data.get('telefone'),
          data.get('email'), data.get('endereco'), data.get('cidade'),
          data.get('estado'), data.get('tipo_id'), data.get('responsavel'),
          data.get('status'), data.get('observacoes'), id))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/fornecedores/<int:id>', methods=['DELETE'])
@login_required
def delete_fornecedor(id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('DELETE FROM fornecedores WHERE id=%s', (id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/tipos-fornecedor', methods=['GET'])
@login_required
def get_tipos_fornecedor():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT * FROM tipos_fornecedor ORDER BY nome')
    result = rows_to_dict(cur)
    cur.close()
    conn.close()
    return jsonify(result)


@app.route('/api/tipos-fornecedor', methods=['POST'])
@login_required
def add_tipo_fornecedor():
    data = request.json
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('INSERT INTO tipos_fornecedor (nome, descricao) VALUES (%s, %s)',
                (data['nome'], data.get('descricao')))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/tipos-fornecedor/<int:id>', methods=['DELETE'])
@login_required
def delete_tipo_fornecedor(id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('DELETE FROM tipos_fornecedor WHERE id=%s', (id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

# ==================== API DASHBOARD ====================

@app.route('/api/dashboard')
@login_required
def get_dashboard():
    conn = get_conn()
    cur = conn.cursor()

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

    cur.execute("SELECT COALESCE(SUM(total), 0) FROM locacoes WHERE status = 'finalizada'")
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
        FROM locacoes WHERE status = 'finalizada'
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

    cur.close()
    conn.close()

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
    conn = get_conn()
    cur = conn.cursor()
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
    cur.close()
    conn.close()

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

        conn = get_conn()
        cur = conn.cursor()

        cur.execute('''
            SELECT TO_CHAR(data_inicio, 'YYYY-MM') as mes
            FROM locacoes WHERE data_inicio >= %s AND data_inicio <= %s
            GROUP BY mes ORDER BY mes
        ''', (data_inicio, data_fim))
        meses = [r[0] for r in cur.fetchall()]

        if not meses:
            cur.close()
            conn.close()
            return jsonify({'resultados': [], 'dados_mensais': []})

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
                cur.execute("SELECT COALESCE(SUM(custo),0) FROM manutencoes WHERE TO_CHAR(data_manutencao,'YYYY-MM')=%s AND status='concluida'", (mes,))
                mm = float(cur.fetchone()[0])
            if inc_abast:
                cur.execute("SELECT COALESCE(SUM(total),0) FROM abastecimentos WHERE TO_CHAR(data_abastecimento,'YYYY-MM')=%s", (mes,))
                ma = float(cur.fetchone()[0])
            if inc_multas:
                cur.execute("SELECT COALESCE(SUM(valor),0) FROM multas WHERE TO_CHAR(data_infracao,'YYYY-MM')=%s AND status='pago'", (mes,))
                mmul = float(cur.fetchone()[0])
            dados_mensais.append({'mes': mes, 'receita': round(mr, 2), 'custos': round(mm + ma + mmul, 2), 'lucro': round(mr - mm - ma - mmul, 2)})

        cur.close()
        conn.close()
        return jsonify({'resultados': resultados, 'dados_mensais': dados_mensais, 'periodo': {'inicio': data_inicio, 'fim': data_fim}})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


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

        conn = get_conn()
        cur = conn.cursor()

        if not fornecedor_id:
            cur.execute('''
                SELECT m.oficina, COUNT(*) as qtd, COALESCE(SUM(m.custo), 0) as total
                FROM manutencoes m
                WHERE m.data_manutencao >= %s AND m.data_manutencao <= %s
                GROUP BY m.oficina ORDER BY total DESC
            ''', (data_inicio, data_fim))
            rows = cur.fetchall()
            resultados = [{'nome': r[0] or 'Sem fornecedor', 'qtd_servicos': r[1], 'total_gasto': round(float(r[2]), 2)} for r in rows if r[0]]
        else:
            cur.execute('SELECT nome FROM fornecedores WHERE id=%s', (fornecedor_id,))
            f = cur.fetchone()
            nome = f[0] if f else ''
            cur.execute('''
                SELECT COUNT(*), COALESCE(SUM(custo),0) FROM manutencoes
                WHERE oficina=%s AND data_manutencao>=%s AND data_manutencao<=%s
            ''', (nome, data_inicio, data_fim))
            r = cur.fetchone()
            resultados = [{'id': fornecedor_id, 'nome': nome, 'qtd_servicos': r[0], 'total_gasto': round(float(r[1]), 2)}]

        cur.close()
        conn.close()
        return jsonify({'resultados': resultados, 'periodo': {'inicio': data_inicio, 'fim': data_fim}})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== API ALERTAS ====================

@app.route('/api/alertas-manutencao')
@login_required
def get_alertas_manutencao():
    conn = get_conn()
    cur = conn.cursor()
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
    cur.close()
    conn.close()
    return jsonify(alertas)


# ==================== API CLIENTES EXTRA ====================

@app.route('/api/clientes/<int:id>/locacoes')
@login_required
def get_locacoes_cliente(id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
        SELECT l.*, v.placa, v.marca, v.modelo
        FROM locacoes l
        LEFT JOIN veiculos v ON l.veiculo_id = v.id
        WHERE l.cliente_id = %s
        ORDER BY l.data_inicio DESC
    ''', (id,))
    result = rows_to_dict(cur)
    cur.close()
    conn.close()
    return jsonify(result)


@app.route('/api/clientes/<int:id>', methods=['GET'])
@login_required
def get_cliente(id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT * FROM clientes WHERE id = %s', (id,))
    result = row_to_dict(cur)
    cur.close()
    conn.close()
    if not result:
        return jsonify({'error': 'Cliente não encontrado'}), 404
    return jsonify(result)


# ==================== API USUÁRIOS ====================

@app.route('/api/usuarios', methods=['GET'])
@login_required
def get_usuarios():
    if current_user.nivel != 'admin':
        return jsonify({'error': 'Acesso negado'}), 403
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT id, nome, email, nivel, ativo, data_cadastro FROM usuarios ORDER BY nome')
    result = rows_to_dict(cur)
    cur.close()
    conn.close()
    return jsonify(result)


@app.route('/api/usuarios', methods=['POST'])
@login_required
def add_usuario():
    if current_user.nivel != 'admin':
        return jsonify({'error': 'Acesso negado'}), 403
    data = request.json
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO usuarios (nome, email, senha_hash, nivel) VALUES (%s, %s, %s, %s)',
            (data['nome'], data['email'].lower(), generate_password_hash(data['senha']), data.get('nivel', 'operador'))
        )
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True})
    except psycopg2.IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'E-mail já cadastrado!'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/usuarios/<int:id>', methods=['PUT'])
@login_required
def update_usuario(id):
    if current_user.nivel != 'admin':
        return jsonify({'error': 'Acesso negado'}), 403
    data = request.json
    try:
        conn = get_conn()
        cur = conn.cursor()
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
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True})
    except psycopg2.IntegrityError:
        conn.rollback()
        conn.close()
        return jsonify({'error': 'E-mail já cadastrado!'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/usuarios/<int:id>', methods=['DELETE'])
@login_required
def delete_usuario(id):
    if current_user.nivel != 'admin':
        return jsonify({'error': 'Acesso negado'}), 403
    if id == current_user.id:
        return jsonify({'error': 'Não é possível excluir o próprio usuário'}), 400
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('UPDATE usuarios SET ativo=FALSE WHERE id=%s', (id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})


if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
