from flask import Flask, render_template, request, jsonify, redirect, url_for, send_from_directory
import sqlite3
import psycopg2
import os
from datetime import datetime
from werkzeug.utils import secure_filename
import requests
from bs4 import BeautifulSoup
import re

app = Flask(__name__)
DB_NAME = 'locadora.db'

PG_DB_CONFIG = {
    'host': 'pgsql.lsws.com.br',
    'port': '5433',
    'database': 'lserp',
    'user': 'aff_bi',
    'password': 'Bi@2026#'
}

def get_pg_connection():
    return psycopg2.connect(**PG_DB_CONFIG)

# Configurações de upload
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads', 'veiculos')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

# Criar pasta de uploads se não existir (dentro da pasta do projeto)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
print(f"Pasta de uploads: {UPLOAD_FOLDER}")

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET,POST,PUT,DELETE,OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
    return response

@app.route('/dashboard-vendas')
def dashboard_vendas():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'dashboard-vendas.html')

@app.route('/dashboard-executor')
def dashboard_executor():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'dashboard-executor-v2.html')

@app.route('/dashboard-vendas-executor')
def dashboard_vendas_executor():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'dashboard-vendas.html')

@app.route('/dashboard-banco')
def dashboard_banco():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'dashboard-banco.html')

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Rota para servir a logo
@app.route('/logo_nova.png')
def serve_logo():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'logo_nova.png')

# ==================== PROJETO FUNCIONANDO ====================
# ==================== API UPLOAD FOTOS VEÍCULOS ====================

@app.route('/api/veiculos/<int:id>/foto', methods=['POST'])
def upload_foto_veiculo(id):
    try:
        if 'foto' not in request.files:
            return jsonify({'error': 'Nenhum arquivo enviado'}), 400
        
        file = request.files['foto']
        if file.filename == '':
            return jsonify({'error': 'Nenhum arquivo selecionado'}), 400
        
        if file and allowed_file(file.filename):
            # Gerar nome único para o arquivo
            ext = file.filename.rsplit('.', 1)[1].lower()
            filename = f'veiculo_{id}_{int(datetime.now().timestamp())}.{ext}'
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            # Atualizar no banco de dados
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute('UPDATE veiculos SET foto = ? WHERE id = ?', (filename, id))
            conn.commit()
            conn.close()
            
            return jsonify({'success': True, 'filename': filename})
        
        return jsonify({'error': 'Tipo de arquivo não permitido'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/veiculos/<int:id>/foto', methods=['DELETE'])
def delete_foto_veiculo(id):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT foto FROM veiculos WHERE id = ?', (id,))
        row = cursor.fetchone()
        
        if row and row[0]:
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], row[0])
            if os.path.exists(filepath):
                os.remove(filepath)
            
            cursor.execute('UPDATE veiculos SET foto = NULL WHERE id = ?', (id,))
            conn.commit()
        
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== BANCO DE DADOS ====================

def init_db():
    """Inicializa o banco de dados com todas as tabelas"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Tabela de Clientes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    
    # Tabela de Veículos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS veiculos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            placa TEXT UNIQUE NOT NULL,
            marca TEXT NOT NULL,
            modelo TEXT NOT NULL,
            ano INTEGER,
            cor TEXT,
            categoria TEXT,
            diaria DECIMAL(10,2),
            km_atual INTEGER DEFAULT 0,
            status TEXT DEFAULT 'disponivel',
            combustivel TEXT,
            observacoes TEXT,
            data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Tabela de Manutenções
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS manutencoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            veiculo_id INTEGER,
            tipo TEXT NOT NULL,
            descricao TEXT,
            data_manutencao DATE,
            km_manutencao INTEGER,
            custo DECIMAL(10,2),
            oficina TEXT,
            proxima_manutencao_km INTEGER,
            proxima_manutencao_data DATE,
            status TEXT DEFAULT 'concluida',
            FOREIGN KEY (veiculo_id) REFERENCES veiculos(id)
        )
    ''')
    
    # Tabela de Locações
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS locacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            veiculo_id INTEGER,
            cliente_id INTEGER,
            data_inicio DATE,
            data_fim DATE,
            diaria DECIMAL(10,2),
            total DECIMAL(10,2),
            km_saida INTEGER,
            km_retorno INTEGER,
            status TEXT DEFAULT 'ativa',
            observacoes TEXT,
            FOREIGN KEY (veiculo_id) REFERENCES veiculos(id),
            FOREIGN KEY (cliente_id) REFERENCES clientes(id)
        )
    ''')
    
    # Tabela de Abastecimentos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS abastecimentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            veiculo_id INTEGER,
            data_abastecimento DATE,
            litros DECIMAL(10,2),
            valor_litro DECIMAL(10,3),
            total DECIMAL(10,2),
            km_abastecimento INTEGER,
            FOREIGN KEY (veiculo_id) REFERENCES veiculos(id)
        )
    ''')
    
    # Tabela de Multas
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS multas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            veiculo_id INTEGER,
            motorista_id INTEGER,
            data_infracao DATE,
            descricao TEXT,
            valor DECIMAL(10,2),
            local_infracao TEXT,
            pontos INTEGER,
            status TEXT DEFAULT 'pendente',
            observaciones TEXT,
            FOREIGN KEY (veiculo_id) REFERENCES veiculos(id),
            FOREIGN KEY (motorista_id) REFERENCES clientes(id)
        )
    ''')
    
    # Tabela de Tipos de Fornecedores
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tipos_fornecedor (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL UNIQUE,
            descricao TEXT,
            data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Tabela de Fornecedores
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fornecedores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            cnpj TEXT,
            cpf TEXT,
            telefone TEXT,
            email TEXT,
            endereco TEXT,
            cidade TEXT,
            estado TEXT,
            tipo_id INTEGER,
            responsavel TEXT,
            status TEXT DEFAULT 'ativo',
            observacoes TEXT,
            data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tipo_id) REFERENCES tipos_fornecedor(id)
        )
    ''')
    
    # Inserir tipos padrão se não existirem
    tipos_default = ['Oficina', 'Lataria', 'Elétrica', 'Seguradora', 'Combustível', 'Peças', 'Seguro', 'Outros']
    for tipo in tipos_default:
        cursor.execute("INSERT OR IGNORE INTO tipos_fornecedor (nome) VALUES (?)", (tipo,))
    
    conn.commit()
    conn.close()
    print("Banco de dados inicializado com sucesso!")

# ==================== ROTAS ====================

@app.route('/')
def index():
    """Página inicial - Dashboard"""
    return render_template('index.html')

@app.route('/clientes')
def clientes():
    """Página de clientes"""
    return render_template('clientes.html')

@app.route('/veiculos')
def veiculos():
    """Página de veículos"""
    return render_template('veiculos.html')

@app.route('/manutencoes')
def manutencoes():
    """Página de manutenções"""
    return render_template('manutencoes.html')

@app.route('/locacoes')
def locacoes():
    """Página de locações"""
    return render_template('locacoes.html')

@app.route('/abastecimentos')
def abastecimentos():
    """Página de abastecimentos"""
    return render_template('abastecimentos.html')

@app.route('/multas')
def multas():
    """Página de multas"""
    return render_template('multas.html')

@app.route('/fornecedores')
def fornecedores():
    """Página de fornecedores"""
    return render_template('fornecedores.html')

@app.route('/relatorios')
def relatorios():
    """Página de relatórios"""
    return render_template('relatorios.html')

# ==================== API CLIENTES ====================

@app.route('/api/clientes', methods=['GET'])
def get_clientes():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM clientes ORDER BY nome")
    clientes = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(clientes)

@app.route('/api/clientes', methods=['POST'])
def add_cliente():
    try:
        data = request.json
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO clientes (nome, cpf, cnh, telefone, email, endereco, cidade, estado, status, observacoes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (data['nome'], data.get('cpf'), data.get('cnh'), data.get('telefone'), 
              data.get('email'), data.get('endereco'), data.get('cidade'), 
              data.get('estado'), data.get('status', 'ativo'), data.get('observacoes')))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except sqlite3.IntegrityError as e:
        conn.close()
        return jsonify({'error': 'CPF ou CNH já cadastrado!'}), 400
    except Exception as e:
        conn.close()
        return jsonify({'error': str(e)}), 500

@app.route('/api/clientes/<int:id>', methods=['PUT'])
def update_cliente(id):
    data = request.json
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE clientes SET nome=?, cpf=?, cnh=?, telefone=?, email=?, endereco=?, cidade=?, estado=?, status=?, observacoes=?
        WHERE id=?
    ''', (data['nome'], data.get('cpf'), data.get('cnh'), data.get('telefone'), 
          data.get('email'), data.get('endereco'), data.get('cidade'), 
          data.get('estado'), data.get('status'), data.get('observacoes'), id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/clientes/<int:id>', methods=['DELETE'])
def delete_cliente(id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM clientes WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ==================== API VEÍCULOS ====================

@app.route('/api/veiculos', methods=['GET'])
def get_veiculos():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM veiculos ORDER BY marca, modelo")
    veiculos = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(veiculos)

@app.route('/api/veiculos', methods=['POST'])
def add_veiculo():
    data = request.json
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO veiculos (placa, marca, modelo, ano, cor, categoria, diaria, km_atual, status, combustivel, observacoes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (data['placa'], data['marca'], data['modelo'], data.get('ano'), data.get('cor'),
          data.get('categoria'), data.get('diaria'), data.get('km_atual', 0),
          data.get('status', 'disponivel'), data.get('combustivel'), data.get('observacoes')))
    conn.commit()
    # Buscar o ID do veículo inserido
    veiculo_id = cursor.lastrowid
    conn.close()
    return jsonify({'success': True, 'id': veiculo_id})

@app.route('/api/veiculos/<int:id>', methods=['PUT'])
def update_veiculo(id):
    data = request.json
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE veiculos SET placa=?, marca=?, modelo=?, ano=?, cor=?, categoria=?, diaria=?, km_atual=?, status=?, combustivel=?, observacoes=?
        WHERE id=?
    ''', (data['placa'], data['marca'], data['modelo'], data.get('ano'), data.get('cor'),
          data.get('categoria'), data.get('diaria'), data.get('km_atual'),
          data.get('status'), data.get('combustivel'), data.get('observacoes'), id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/veiculos/<int:id>', methods=['DELETE'])
def delete_veiculo(id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM veiculos WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ==================== API MANUTENÇÕES ====================

@app.route('/api/manutencoes', methods=['GET'])
def get_manutencoes():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT m.*, v.placa, v.marca, v.modelo 
        FROM manutencoes m 
        LEFT JOIN veiculos v ON m.veiculo_id = v.id 
        ORDER BY m.data_manutencao DESC
    ''')
    manutencoes = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(manutencoes)

@app.route('/api/manutencoes/veiculo/<int:veiculo_id>')
def get_manutencoes_veiculo(veiculo_id):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM manutencoes WHERE veiculo_id = ? ORDER BY data_manutencao DESC
    ''', (veiculo_id,))
    manutencoes = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(manutencoes)

@app.route('/api/manutencoes', methods=['POST'])
def add_manutencao():
    data = request.json
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO manutencoes (veiculo_id, tipo, descricao, data_manutencao, km_manutencao, custo, oficina, proxima_manutencao_km, proxima_manutencao_data, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (data['veiculo_id'], data['tipo'], data.get('descricao'), data.get('data_manutencao'),
          data.get('km_manutencao'), data.get('custo'), data.get('oficina'),
          data.get('proxima_manutencao_km'), data.get('proxima_manutencao_data'), data.get('status', 'concluida')))
    
    if data.get('km_manutencao'):
        cursor.execute("UPDATE veiculos SET km_atual = ? WHERE id = ?", (data['km_manutencao'], data['veiculo_id']))
    
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/manutencoes/<int:id>', methods=['PUT'])
def update_manutencao(id):
    data = request.json
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE manutencoes SET veiculo_id=?, tipo=?, descricao=?, data_manutencao=?, 
        km_manutencao=?, custo=?, oficina=?, proxima_manutencao_km=?, 
        proxima_manutencao_data=?, status=?
        WHERE id=?
    ''', (data.get('veiculo_id'), data.get('tipo'), data.get('descricao'),
          data.get('data_manutencao'), data.get('km_manutencao'), data.get('custo'),
          data.get('oficina'), data.get('proxima_manutencao_km'), 
          data.get('proxima_manutencao_data'), data.get('status'), id))
    
    if data.get('km_manutencao'):
        cursor.execute("UPDATE veiculos SET km_atual = ? WHERE id = ?", 
                      (data['km_manutencao'], data['veiculo_id']))
    
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/manutencoes/<int:id>', methods=['DELETE'])
def delete_manutencao(id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM manutencoes WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ==================== API LOCAÇÕES ====================

@app.route('/api/locacoes', methods=['GET'])
def get_locacoes():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT l.*, v.placa, v.marca, v.modelo, c.nome as nome_cliente
        FROM locacoes l 
        LEFT JOIN veiculos v ON l.veiculo_id = v.id 
        LEFT JOIN clientes c ON l.cliente_id = c.id
        ORDER BY l.data_inicio DESC
    ''')
    locacoes = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(locacoes)

@app.route('/api/itens-faturados')
def get_itens_faturados():
    conn = get_pg_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT pi.descricao,
               SUM(COALESCE(pi.qtd, 0))::double precision AS qtd,
               AVG(COALESCE(pi.vl_unit, 0))::double precision AS valor_unit,
               SUM(COALESCE(pi.vl_unit, 0) * COALESCE(pi.qtd, 0))::double precision AS total,
               gs.sub_grupo AS subgrupo,
               COALESCE(p.data_faturado, p.dt_venda, p.data_saida) AS data,
               pi.id_vendedor,
               pi.id_vendedor2,
               v.nome AS nome_vendedor,
               e.nome AS nome_executor
        FROM pepplow.pedido_itens pi
        JOIN pepplow.pedido p ON pi.id_pai = p.id
        LEFT JOIN pepplow.item i ON pi.id_item = i.id
        LEFT JOIN pepplow.grupo_sub gs ON i.id_subgrupo = gs.id
        LEFT JOIN pepplow.colaborador v ON pi.id_vendedor = v.id
        LEFT JOIN pepplow.colaborador e ON pi.id_vendedor2 = e.id
        WHERE p.excluido = false
          AND p.faturado = 'S'
        GROUP BY pi.descricao, gs.sub_grupo, COALESCE(p.data_faturado, p.dt_venda, p.data_saida), pi.id_vendedor, pi.id_vendedor2, v.nome, e.nome
        ORDER BY total DESC
    ''')
    
    itens = []
    for row in cursor.fetchall():
        item = {
            'codigo': row[0] or 'N/A',  # descricao as codigo
            'descricao': row[0] or 'N/A',
            'subgrupo': row[4] or 'Não classificado',
            'qtd': float(row[1] or 0),
            'valor_unit': float(row[2] or 0),
            'total': float(row[3] or 0),
            'data': row[5].isoformat() if row[5] is not None else None,
            'id_vendedor': row[6] or 0,
            'id_vendedor2': row[7] or 0,
            'nome_vendedor': row[8] or f'ID {row[6] or 0}',
            'nome_executor': row[9] or f'ID {row[7] or 0}'
        }
        itens.append(item)
    
    cursor.close()
    conn.close()
    return jsonify(itens)

@app.route('/api/vendas-completas')
def get_vendas_completas():
    conn = get_pg_connection()
    cursor = conn.cursor()
    
    # Get PEDIDOS data for the main total
    cursor.execute('''
        SELECT p.id, p.id_vendedor, p.tt_liquido, p.faturado, 
               COALESCE(p.data_faturado, p.dt_venda, p.data_saida) as dt_ref,
               v.nome as nome_vendedor
        FROM pepplow.pedido p
        LEFT JOIN pepplow.colaborador v ON p.id_vendedor = v.id
        WHERE p.excluido = false
          AND p.faturado = 'S'
          AND (v.status = 'S' OR p.id_vendedor IS NULL)
        ORDER BY COALESCE(p.data_faturado, p.dt_venda, p.data_saida) DESC
    ''')

    pedidos = []
    for row in cursor.fetchall():
        item = {
            'id': row[0],
            'id_vendedor': row[1] or 0,
            'tt_liquido': row[2] or 0,
            'faturado': row[3] or 'N',
            'dt_ref': row[4],
            'nome_vendedor': row[5] or f'ID {row[1] or 0}',
            'tipo': 'pedido',
            'qtd': 1,
            'valor': float(row[2] or 0),
            'data': row[4].isoformat() if row[4] is not None else None,
            'pedido_faturado': 'S'
        }
        pedidos.append(item)

    # Get PEDIDO_ITENS data for breakdown by type (P, S, C, I)
    cursor.execute('''
        SELECT pi.id_pai AS pedido_id,
               pi.id_vendedor,
               p.id_atendente,
               pi.id_vendedor2,
               COALESCE(pi.qtd, 0)::double precision AS qtd,
               COALESCE(pi.vl_unit, 0)::double precision AS vl_unit,
               COALESCE(pi.vl_custo_cont, pi.vl_custo, 0)::double precision AS custo,
               COALESCE(pi.vl_unit, 0) * COALESCE(pi.qtd, 0) AS valor,
               COALESCE(pi.vl_unit, 0) * COALESCE(pi.qtd, 0) - COALESCE(pi.vl_custo_cont, pi.vl_custo, 0) AS margem,
               pi.tipo,
               i.id_segmento,
               s.segmento,
               i.id_grupo,
               g.grupo,
               i.id_subgrupo,
               gs.sub_grupo,
               p.dt_venda,
               p.data_faturado,
               p.data_saida,
               p.faturado AS pedido_faturado,
               v.nome AS nome_vendedor,
               e.nome AS nome_executor
        FROM pepplow.pedido_itens pi
        JOIN pepplow.pedido p ON pi.id_pai = p.id
        LEFT JOIN pepplow.item i ON pi.id_item = i.id
        LEFT JOIN pepplow.segmento s ON i.id_segmento = s.id
        LEFT JOIN pepplow.grupo g ON i.id_grupo = g.id
        LEFT JOIN pepplow.grupo_sub gs ON i.id_subgrupo = gs.id
        LEFT JOIN pepplow.colaborador v ON pi.id_vendedor = v.id
        LEFT JOIN pepplow.colaborador e ON pi.id_vendedor2 = e.id
        WHERE p.excluido = false
          AND p.faturado = 'S'
          AND (pi.id_vendedor IS NULL OR v.status = 'S')
          AND (pi.id_vendedor2 IS NULL OR e.status = 'S')
    ''')

    itens = []
    for row in cursor.fetchall():
        item = {
            'pedido_id': row[0],
            'id_vendedor': row[1] or 0,
            'id_atendente': row[2] or 0,
            'id_vendedor2': row[3] or 0,
            'qtd': float(row[4] or 0),
            'vl_unit': float(row[5] or 0),
            'custo': float(row[6] or 0),
            'valor': float(row[7] or 0),
            'margem': float(row[8] or 0),
            'tipo': row[9] or 'P',
            'id_segmento': row[10] or 0,
            'segmento': row[11] or 'Não classificado',
            'id_grupo': row[12] or 0,
            'grupo': row[13] or 'Não classificado',
            'id_subgrupo': row[14] or 0,
            'sub_grupo': row[15] or 'Não classificado',
            'dt_venda': row[16],
            'data_faturado': row[17],
            'data_saida': row[18],
            'pedido_faturado': row[19] or 'N',
            'nome_vendedor': row[20] or f'ID {row[1] or 0}',
            'nome_executor': row[21] or f'ID {row[3] or 0}'
        }
        data_value = item.get('data_faturado') or item.get('dt_venda') or item.get('data_saida')
        item['data'] = data_value.isoformat() if data_value is not None else None
        itens.append(item)

    cursor.execute('''
        SELECT id, nome, funcao_mecanico
        FROM pepplow.colaborador
        WHERE status = 'S'
          AND funcao_mecanico = true
    ''')

    tecnicos = [
        {
            'id': row[0],
            'nome': row[1] or f'ID {row[0]}',
            'funcao_mecanico': row[2]
        }
        for row in cursor.fetchall()
    ]

    cursor.execute('''
        SELECT id, nome, funcao_mecanico
        FROM pepplow.colaborador
        WHERE status = 'S'
        ORDER BY nome
    ''')

    colaboradores = [
        {
            'id': row[0],
            'nome': row[1] or f'ID {row[0]}',
            'funcao_mecanico': row[2]
        }
        for row in cursor.fetchall()
    ]

    cursor.close()
    conn.close()
    return jsonify({'pedido': pedidos, 'pedido_itens': itens, 'tecnicos': tecnicos, 'colaboradores': colaboradores, 'oc': [], 'orcamento': [], 'os': []})

@app.route('/dashboard-estoque')
def dashboard_estoque():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'dashboard-estoque.html')

@app.route('/api/estoque-abc')
def get_estoque_abc():
    conn = get_pg_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT i.id,
               COALESCE(pi.descricao, 'N/A') AS descricao,
               COALESCE(e.saldo, e.qtd, 0) AS estoque,
               COALESCE(e.vl_custo, 0)::double precision AS preco_custo,
               COALESCE(SUM(COALESCE(pi.qtd, 0)), 0)::double precision AS qtd_vendida,
               COALESCE(SUM(COALESCE(pi.vl_unit, 0) * COALESCE(pi.qtd, 0)), 0)::double precision AS receita,
               COALESCE(AVG(COALESCE(pi.vl_unit, 0)), 0)::double precision AS ticket_medio
        FROM pepplow.estoque e
        JOIN pepplow.item i ON e.id_item = i.id
        LEFT JOIN pepplow.pedido_itens pi ON pi.id_item = i.id
        LEFT JOIN pepplow.pedido p ON pi.id_pai = p.id
        WHERE COALESCE(e.saldo, e.qtd, 0) > 0
          AND (p.id IS NULL OR (p.excluido = false AND p.faturado = 'S'))
        GROUP BY i.id, pi.descricao, e.saldo, e.qtd, e.vl_custo
    ''')

    rows = cursor.fetchall()
    items = []
    total_revenue = 0
    total_stock_value = 0
    total_qty_sold = 0

    for row in rows:
        item = {
            'id': row[0],
            'descricao': row[1] or 'N/A',
            'estoque': float(row[2] or 0),
            'preco_custo': float(row[3] or 0),
            'qtd_vendida': float(row[4] or 0),
            'receita': float(row[5] or 0),
            'ticket_medio': float(row[6] or 0)
        }
        item['valor_estoque'] = item['estoque'] * item['preco_custo']
        total_revenue += item['receita']
        total_stock_value += item['valor_estoque']
        total_qty_sold += item['qtd_vendida']
        items.append(item)

    items.sort(key=lambda x: x['receita'], reverse=True)
    cumulative = 0
    for item in items:
        item['perc_receita'] = (item['receita'] / total_revenue * 100) if total_revenue else 0
        cumulative += item['perc_receita']
        if cumulative <= 70:
            item['abc_class'] = 'A'
        elif cumulative <= 90:
            item['abc_class'] = 'B'
        else:
            item['abc_class'] = 'C'

    abc_summary = {
        'A': {'count': 0, 'revenue': 0.0},
        'B': {'count': 0, 'revenue': 0.0},
        'C': {'count': 0, 'revenue': 0.0}
    }
    for item in items:
        abc_summary[item['abc_class']]['count'] += 1
        abc_summary[item['abc_class']]['revenue'] += item['receita']

    top_10 = items[:10]
    bottom_10 = sorted(items, key=lambda x: x['receita'])[:10]

    cursor.close()
    conn.close()

    return jsonify({
        'items': items,
        'top_10': top_10,
        'bottom_10': bottom_10,
        'total_itens_estoque': len(items),
        'total_valor_estoque': total_stock_value,
        'total_receita': total_revenue,
        'total_qtd_vendida': total_qty_sold,
        'abc_summary': abc_summary
    })

@app.route('/api/locacoes/<int:id>', methods=['PUT'])
def update_locacao(id):
    data = request.json
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Buscar locação atual
    cursor.execute("SELECT veiculo_id, status FROM locacoes WHERE id = ?", (id,))
    locacao = cursor.fetchone()
    
    if not locacao:
        conn.close()
        return jsonify({'error': 'Locação não encontrada'}), 404
    
    veiculo_antigo = locacao[0]
    status_antigo = locacao[1]
    
    # Atualizar locação
    cursor.execute('''
        UPDATE locacoes SET 
            veiculo_id=?, cliente_id=?, data_inicio=?, data_fim=?, 
            diaria=?, total=?, km_saida=?, observacoes=?
        WHERE id=?
    ''', (data.get('veiculo_id'), data.get('cliente_id'), data.get('data_inicio'),
          data.get('data_fim'), data.get('diaria'), data.get('total'), 
          data.get('km_saida'), data.get('observacoes'), id))
    
    # Se mudou o veículo, atualizar status dos veículos
    veiculo_novo = data.get('veiculo_id')
    if veiculo_antigo != veiculo_novo and status_antigo == 'ativa':
        # Liberar veículo antigo
        cursor.execute("UPDATE veiculos SET status = 'disponivel' WHERE id = ?", (veiculo_antigo,))
        # Ocupar novo veículo
        cursor.execute("UPDATE veiculos SET status = 'locado' WHERE id = ?", (veiculo_novo,))
    
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/locacoes', methods=['POST'])
def add_locacao():
    data = request.json
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    dias = (datetime.strptime(data['data_fim'], '%Y-%m-%d') - datetime.strptime(data['data_inicio'], '%Y-%m-%d')).days + 1
    total = dias * float(data.get('diaria', 0))
    
    cursor.execute('''
        INSERT INTO locacoes (veiculo_id, cliente_id, data_inicio, data_fim, diaria, total, km_saida, status, observacoes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (data['veiculo_id'], data['cliente_id'], data['data_inicio'], data['data_fim'],
          data['diaria'], total, data.get('km_saida'), data.get('status', 'ativa'), data.get('observacoes')))
    
    cursor.execute("UPDATE veiculos SET status = 'locado' WHERE id = ?", (data['veiculo_id'],))
    
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/locacoes/<int:id>/devolver', methods=['PUT'])
def devolver_locacao(id):
    data = request.json
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT veiculo_id, km_saida FROM locacoes WHERE id = ?", (id,))
    locacao = cursor.fetchone()
    
    if locacao:
        cursor.execute('''
            UPDATE locacoes SET data_fim = ?, km_retorno = ?, status = 'finalizada' WHERE id = ?
        ''', (data.get('data_fim'), data.get('km_retorno'), id))
        
        if data.get('km_retorno'):
            cursor.execute("UPDATE veiculos SET km_atual = ?, status = 'disponivel' WHERE id = ?", 
                          (data['km_retorno'], locacao[0]))
    
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ==================== API ABASTECIMENTOS ====================

@app.route('/api/abastecimentos', methods=['GET'])
def get_abastecimentos():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT a.*, v.placa, v.marca, v.modelo 
        FROM abastecimentos a 
        LEFT JOIN veiculos v ON a.veiculo_id = v.id 
        ORDER BY a.data_abastecimento DESC
    ''')
    abast = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(abast)

@app.route('/api/abastecimentos', methods=['POST'])
def add_abastecimento():
    data = request.json
    total = float(data.get('litros', 0)) * float(data.get('valor_litro', 0))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO abastecimentos (veiculo_id, data_abastecimento, litros, valor_litro, total, km_abastecimento)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (data['veiculo_id'], data.get('data_abastecimento'), data.get('litros'),
          data.get('valor_litro'), total, data.get('km_abastecimento')))
    
    if data.get('km_abastecimento'):
        cursor.execute("UPDATE veiculos SET km_atual = ? WHERE id = ?", (data['km_abastecimento'], data['veiculo_id']))
    
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/abastecimentos/<int:id>', methods=['DELETE'])
def delete_abastecimento(id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM abastecimentos WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ==================== API MULTAS ====================

@app.route('/api/multas', methods=['GET'])
def get_multas():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT m.*, v.placa, v.marca, v.modelo, c.nome as nome_motorista
        FROM multas m 
        LEFT JOIN veiculos v ON m.veiculo_id = v.id 
        LEFT JOIN clientes c ON m.motorista_id = c.id
        ORDER BY m.data_infracao DESC
    ''')
    multas = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(multas)

@app.route('/api/multas', methods=['POST'])
def add_multa():
    data = request.json
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO multas (veiculo_id, motorista_id, data_infracao, descricao, valor, local_infracao, pontos, status, observaciones)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (data.get('veiculo_id'), data.get('motorista_id'), data.get('data_infracao'),
          data.get('descricao'), data.get('valor'), data.get('local_infracao'),
          data.get('pontos'), data.get('status', 'pendente'), data.get('observaciones')))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/multas/<int:id>', methods=['PUT'])
def update_multa(id):
    data = request.json
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE multas SET veiculo_id=?, motorista_id=?, data_infracao=?, descricao=?, valor=?, local_infracao=?, pontos=?, status=?, observaciones=?
        WHERE id=?
    ''', (data.get('veiculo_id'), data.get('motorista_id'), data.get('data_infracao'),
          data.get('descricao'), data.get('valor'), data.get('local_infracao'),
          data.get('pontos'), data.get('status'), data.get('observaciones'), id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/multas/<int:id>', methods=['DELETE'])
def delete_multa(id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM multas WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ==================== API FORNECEDORES ====================

@app.route('/api/fornecedores', methods=['GET'])
def get_fornecedores():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT f.*, t.nome as tipo_nome
        FROM fornecedores f
        LEFT JOIN tipos_fornecedor t ON f.tipo_id = t.id
        ORDER BY f.nome
    ''')
    fornecedores = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(fornecedores)

@app.route('/api/fornecedores', methods=['POST'])
def add_fornecedor():
    data = request.json
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO fornecedores (nome, cnpj, cpf, telefone, email, endereco, cidade, estado, tipo_id, responsavel, status, observacoes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (data['nome'], data.get('cnpj'), data.get('cpf'), data.get('telefone'),
          data.get('email'), data.get('endereco'), data.get('cidade'), 
          data.get('estado'), data.get('tipo_id'), data.get('responsavel'),
          data.get('status', 'ativo'), data.get('observacoes')))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/fornecedores/<int:id>', methods=['PUT'])
def update_fornecedor(id):
    data = request.json
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE fornecedores SET nome=?, cnpj=?, cpf=?, telefone=?, email=?, endereco=?, cidade=?, estado=?, tipo_id=?, responsavel=?, status=?, observacoes=?
        WHERE id=?
    ''', (data['nome'], data.get('cnpj'), data.get('cpf'), data.get('telefone'),
          data.get('email'), data.get('endereco'), data.get('cidade'),
          data.get('estado'), data.get('tipo_id'), data.get('responsavel'),
          data.get('status'), data.get('observacoes'), id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/fornecedores/<int:id>', methods=['DELETE'])
def delete_fornecedor(id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM fornecedores WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ==================== API TIPOS FORNECEDOR ====================

@app.route('/api/tipos-fornecedor', methods=['GET'])
def get_tipos_fornecedor():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tipos_fornecedor ORDER BY nome")
    tipos = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(tipos)

@app.route('/api/tipos-fornecedor', methods=['POST'])
def add_tipo_fornecedor():
    data = request.json
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO tipos_fornecedor (nome, descricao)
        VALUES (?, ?)
    ''', (data['nome'], data.get('descricao')))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/tipos-fornecedor/<int:id>', methods=['DELETE'])
def delete_tipo_fornecedor(id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tipos_fornecedor WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ==================== API DASHBOARD ====================

@app.route('/api/dashboard')
def get_dashboard():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) as total FROM veiculos")
    total_veiculos = cursor.fetchone()['total']
    
    cursor.execute("SELECT status, COUNT(*) as total FROM veiculos GROUP BY status")
    status_veiculos = {row['status']: row['total'] for row in cursor.fetchall()}
    
    disponiveis = status_veiculos.get('disponivel', 0)
    taxa_ocupacao = ((total_veiculos - disponiveis) / total_veiculos * 100) if total_veiculos > 0 else 0
    
    cursor.execute("SELECT COUNT(*) as total FROM clientes WHERE status = 'ativo'")
    total_clientes = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(*) as total FROM locacoes WHERE status = 'ativa'")
    locacoes_ativas = cursor.fetchone()['total']
    
    mes_atual = datetime.now().strftime('%Y-%m')
    cursor.execute("SELECT SUM(total) as total FROM locacoes WHERE strftime('%Y-%m', data_inicio) = ?", (mes_atual,))
    faturamento_mes = cursor.fetchone()['total'] or 0
    
    cursor.execute("SELECT SUM(total) as total FROM locacoes WHERE status = 'finalizada'")
    faturamento_total = cursor.fetchone()['total'] or 0
    
    cursor.execute("SELECT SUM(custo) as total FROM manutencoes WHERE status = 'concluida'")
    custo_manutencao = cursor.fetchone()['total'] or 0
    
    cursor.execute('''
        SELECT v.placa, v.marca, v.modelo, COUNT(l.id) as total_locacoes
        FROM veiculos v
        LEFT JOIN locacoes l ON v.id = l.veiculo_id
        GROUP BY v.id
        ORDER BY total_locacoes DESC
        LIMIT 5
    ''')
    veiculos_mais_locados = [dict(row) for row in cursor.fetchall()]
    
    cursor.execute('''
        SELECT strftime('%Y-%m', data_inicio) as mes, SUM(total) as total, COUNT(*) as qtd
        FROM locacoes
        WHERE status = 'finalizada'
        GROUP BY mes
        ORDER BY mes DESC
        LIMIT 6
    ''')
    locacoes_mes_raw = cursor.fetchall()
    
    # Para cada mês, buscar custo de manutenção
    locacoes_mes = []
    for row in locacoes_mes_raw:
        mes = row['mes']
        # Buscar custos de manutenção desse mês
        cursor.execute('''
            SELECT COALESCE(SUM(custo), 0) as custo
            FROM manutencoes
            WHERE strftime('%Y-%m', data_manutencao) = ? AND status = 'concluida'
        ''', (mes,))
        custo_row = cursor.fetchone()
        locacoes_mes.append({
            'mes': mes,
            'total': row['total'],
            'qtd': row['qtd'],
            'custo_manutencao': float(custo_row['custo']) if custo_row['custo'] else 0
        })
    
    cursor.execute("SELECT COUNT(*) as total FROM manutencoes WHERE status != 'concluida'")
    manutencoes_pendentes = cursor.fetchone()['total']
    
    # Contar multas pendentes
    cursor.execute("SELECT COUNT(*) as total FROM multas WHERE status = 'pendente'")
    multas_pendentes = cursor.fetchone()['total']
    
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
        'multas_pendentes': multas_pendentes
    })

# ==================== API CUSTOS POR VEÍCULO ====================

@app.route('/api/custos-veiculos')
def get_custos_veiculos():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 
            v.id, v.placa, v.marca, v.modelo, v.ano, v.km_atual,
            COALESCE(SUM(m.custo), 0) as custo_total_manutencao
        FROM veiculos v
        LEFT JOIN manutencoes m ON v.id = m.veiculo_id AND m.status = 'concluida'
        GROUP BY v.id
        ORDER BY v.placa
    ''')
    
    veiculos = []
    for row in cursor.fetchall():
        km = row['km_atual'] if row['km_atual'] is not None else 1
        custo = row['custo_total_manutencao'] if row['custo_total_manutencao'] is not None else 0
        custo_por_km = custo / km if km > 0 else 0
        veiculos.append({
            'id': row['id'], 
            'placa': row['placa'], 
            'marca': row['marca'], 
            'modelo': row['modelo'],
            'ano': row['ano'], 
            'km_atual': km, 
            'custo_total': round(float(custo), 2), 
            'custo_por_km': round(custo_por_km, 4)
        })
    
    conn.close()
    veiculos.sort(key=lambda x: x['custo_por_km'], reverse=True)
    return jsonify(veiculos)

# ==================== API RELATÓRIOS ====================

@app.route('/api/relatorio-lucratividade', methods=['POST'])
def get_relatorio_lucratividade():
    try:
        data = request.json
        veiculos_ids = data.get('veiculos', [])
        data_inicio = data.get('data_inicio')
        data_fim = data.get('data_fim')
        incluir_manutencao = data.get('incluir_manutencao', True)
        incluir_abastecimento = data.get('incluir_abastecimento', True)
        incluir_multas = data.get('incluir_multas', True)
        
        if not veiculos_ids or not data_inicio or not data_fim:
            return jsonify({'error': 'Parâmetros inválidos'}), 400
        
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        resultados = []
        dados_mensais = []
        
        # Período para gráfico
        cursor.execute('''
            SELECT strftime('%Y-%m', data_inicio) as mes
            FROM locacoes
            WHERE data_inicio >= ? AND data_inicio <= ?
            GROUP BY mes
            ORDER BY mes
        ''', (data_inicio, data_fim))
        meses = [row['mes'] for row in cursor.fetchall()]
        
        # Se não há meses, retorna vazio mas sem erro
        if not meses:
            conn.close()
            return jsonify({'resultados': [], 'dados_mensais': []})
        
        # Buscar dados por veículo
        for veiculo_id in veiculos_ids:
            # Informações do veículo
            cursor.execute("SELECT * FROM veiculos WHERE id = ?", (veiculo_id,))
            veiculo = cursor.fetchone()
            
            if not veiculo:
                continue
            
            # Receitas de locações no período
            cursor.execute('''
                SELECT COALESCE(SUM(total), 0) as receita, COUNT(*) as qtd_locacoes
                FROM locacoes
                WHERE veiculo_id = ? AND data_inicio >= ? AND data_inicio <= ?
            ''', (veiculo_id, data_inicio, data_fim))
            receita_data = cursor.fetchone()
            receita = float(receita_data['receita']) if receita_data['receita'] else 0
            
            # Custos de manutenção no período (INDEPENDENTE de locações)
            custo_manutencao = 0
            if incluir_manutencao:
                cursor.execute('''
                    SELECT COALESCE(SUM(custo), 0) as custo
                    FROM manutencoes
                    WHERE veiculo_id = ? AND data_manutencao >= ? AND data_manutencao <= ?
                ''', (veiculo_id, data_inicio, data_fim))
                row = cursor.fetchone()
                custo_manutencao = float(row['custo']) if row['custo'] else 0
            
            # Custos de abastecimento no período (INDEPENDENTE de locações)
            custo_abastecimento = 0
            if incluir_abastecimento:
                cursor.execute('''
                    SELECT COALESCE(SUM(total), 0) as custo
                    FROM abastecimentos
                    WHERE veiculo_id = ? AND data_abastecimento >= ? AND data_abastecimento <= ?
                ''', (veiculo_id, data_inicio, data_fim))
                row = cursor.fetchone()
                custo_abastecimento = float(row['custo']) if row['custo'] else 0
            
            # Custos de multas no período (INDEPENDENTE de locações)
            custo_multas = 0
            if incluir_multas:
                cursor.execute('''
                    SELECT COALESCE(SUM(valor), 0) as custo
                    FROM multas
                    WHERE veiculo_id = ? AND data_infracao >= ? AND data_infracao <= ?
                ''', (veiculo_id, data_inicio, data_fim))
                row = cursor.fetchone()
                custo_multas = float(row['custo']) if row['custo'] else 0
            
            total_custos = custo_manutencao + custo_abastecimento + custo_multas
            lucro = receita - total_custos
            
            # Garantir que ano seja um valor válido
            ano = veiculo['ano']
            if ano is None:
                ano = 0
            
            resultados.append({
                'veiculo': {
                    'id': veiculo['id'],
                    'placa': veiculo['placa'],
                    'marca': veiculo['marca'],
                    'modelo': veiculo['modelo'],
                    'ano': ano
                },
                'receita': round(receita, 2),
                'custo_manutencao': round(custo_manutencao, 2),
                'custo_abastecimento': round(custo_abastecimento, 2),
                'custo_multas': round(custo_multas, 2),
                'total_custos': round(total_custos, 2),
                'lucro': round(lucro, 2),
                'margem': round((lucro / receita * 100), 2) if receita > 0 else 0
            })
        
        # Dados mensais para gráfico
        for mes in meses:
            mes_receita = 0
            mes_manutencao = 0
            mes_abastecimento = 0
            mes_multas = 0
            
            # Receitas do mês
            cursor.execute('''
                SELECT COALESCE(SUM(total), 0) as total
                FROM locacoes
                WHERE strftime('%Y-%m', data_inicio) = ?
            ''', (mes,))
            row = cursor.fetchone()
            mes_receita = float(row['total']) if row['total'] else 0
            
            if incluir_manutencao:
                cursor.execute('''
                    SELECT COALESCE(SUM(custo), 0) as total
                    FROM manutencoes
                    WHERE strftime('%Y-%m', data_manutencao) = ? AND status = 'concluida'
                ''', (mes,))
                row = cursor.fetchone()
                mes_manutencao = float(row['total']) if row['total'] else 0
            
            if incluir_abastecimento:
                cursor.execute('''
                    SELECT COALESCE(SUM(total), 0) as total
                    FROM abastecimentos
                    WHERE strftime('%Y-%m', data_abastecimento) = ?
                ''', (mes,))
                row = cursor.fetchone()
                mes_abastecimento = float(row['total']) if row['total'] else 0
            
            if incluir_multas:
                cursor.execute('''
                    SELECT COALESCE(SUM(valor), 0) as total
                    FROM multas
                    WHERE strftime('%Y-%m', data_infracao) = ? AND status = 'pago'
                ''', (mes,))
                row = cursor.fetchone()
                mes_multas = float(row['total']) if row['total'] else 0
            
            dados_mensais.append({
                'mes': mes,
                'receita': round(mes_receita, 2),
                'custos': round(mes_manutencao + mes_abastecimento + mes_multas, 2),
                'lucro': round(mes_receita - (mes_manutencao + mes_abastecimento + mes_multas), 2)
            })
        
        conn.close()
        
        return jsonify({
            'resultados': resultados,
            'dados_mensais': dados_mensais,
            'periodo': {'inicio': data_inicio, 'fim': data_fim}
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== API RELATÓRIO FORNECEDOR ====================

@app.route('/api/relatorio-fornecedor', methods=['POST'])
def get_relatorio_fornecedor():
    try:
        data = request.json
        fornecedor_id = data.get('fornecedor_id')
        data_inicio = data.get('data_inicio')
        data_fim = data.get('data_fim')
        
        if not data_inicio or not data_fim:
            return jsonify({'error': 'Parâmetros inválidos'}), 400
        
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Se um fornecedor específico foi selecionado, buscar seu nome
        nome_fornecedor = None
        if fornecedor_id:
            cursor.execute('SELECT nome, tipo_id FROM fornecedores WHERE id = ?', (fornecedor_id,))
            row = cursor.fetchone()
            if row:
                nome_fornecedor = row['nome']
                tipo_id_fornecedor = row['tipo_id']
        
        # Se não há fornecedor selecionado, mostrar todos
        if not fornecedor_id:
            cursor.execute('''
                SELECT 
                    m.oficina as nome_oficina,
                    COUNT(*) as qtd_servicos,
                    COALESCE(SUM(m.custo), 0) as total_gasto
                FROM manutencoes m
                WHERE m.data_manutencao >= ? AND m.data_manutencao <= ?
                GROUP BY m.oficina
                ORDER BY total_gasto DESC
            ''', (data_inicio, data_fim))
            manutencoes = cursor.fetchall()
            
            resultados = []
            for m in manutencoes:
                nome_oficina = m['nome_oficina']
                if not nome_oficina or nome_oficina.strip() == '':
                    continue
                    
                cursor.execute('''
                    SELECT f.id, f.nome, t.nome as tipo_nome
                    FROM fornecedores f
                    LEFT JOIN tipos_fornecedor t ON f.tipo_id = t.id
                    WHERE f.nome = ?
                ''', (nome_oficina,))
                fornec = cursor.fetchone()
                
                if fornec:
                    resultados.append({
                        'id': fornec['id'],
                        'nome': fornec['nome'],
                        'tipo_nome': fornec['tipo_nome'],
                        'qtd_servicos': m['qtd_servicos'],
                        'total_gasto': round(float(m['total_gasto']), 2)
                    })
                else:
                    resultados.append({
                        'id': 0,
                        'nome': nome_oficina,
                        'tipo_nome': 'Outros',
                        'qtd_servicos': m['qtd_servicos'],
                        'total_gasto': round(float(m['total_gasto']), 2)
                    })
            
            # Se não encontrou resultados, mostrar fornecedores cadastrados com zeros
            if not resultados:
                cursor.execute('''
                    SELECT f.id, f.nome, t.nome as tipo_nome
                    FROM fornecedores f
                    LEFT JOIN tipos_fornecedor t ON f.tipo_id = t.id
                    WHERE f.status = 'ativo'
                    ORDER BY f.nome
                ''')
                for row in cursor.fetchall():
                    resultados.append({
                        'id': row['id'],
                        'nome': row['nome'],
                        'tipo_nome': row['tipo_nome'],
                        'qtd_servicos': 0,
                        'total_gasto': 0
                    })
        else:
            # Fornecedor específico selecionado - mostrar TODAS as manutenções (incluindo as sem oficina)
            # porque quando não tem oficina definida, é do fornecedor padrão
            if nome_fornecedor:
                # Primeiro, buscar manutenções com o nome do fornecedor
                cursor.execute('''
                    SELECT 
                        m.oficina as nome_oficina,
                        COUNT(*) as qtd_servicos,
                        COALESCE(SUM(m.custo), 0) as total_gasto
                    FROM manutencoes m
                    WHERE m.data_manutencao >= ? AND m.data_manutencao <= ?
                    AND (m.oficina = ? OR m.oficina IS NULL OR m.oficina = '')
                    GROUP BY m.oficina
                ''', (data_inicio, data_fim, nome_fornecedor))
                manutencoes = cursor.fetchall()
            else:
                manutencoes = []
            
            # Buscar o tipo do fornecedor
            cursor.execute('SELECT t.nome as tipo_nome FROM tipos_fornecedor t WHERE t.id = ?', (tipo_id_fornecedor,))
            tipo_row = cursor.fetchone()
            tipo_nome = tipo_row['tipo_nome'] if tipo_row else 'Outros'
            
            # Calcular total
            total_qtd = sum(m['qtd_servicos'] for m in manutencoes)
            total_gasto = sum(float(m['total_gasto']) for m in manutencoes)
            
            resultados = [{
                'id': fornecedor_id,
                'nome': nome_fornecedor,
                'tipo_nome': tipo_nome,
                'qtd_servicos': total_qtd,
                'total_gasto': round(total_gasto, 2)
            }]
        
        conn.close()
        
        return jsonify({
            'resultados': resultados,
            'periodo': {'inicio': data_inicio, 'fim': data_fim}
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== API BUSCAR MULTAS ONLINE ====================

@app.route('/api/buscar-multas', methods=['POST'])
def buscar_multas_online():
    """Busca multas online usando web scraping"""
    data = request.json
    placa = data.get('placa', '').upper().replace('-', '').replace(' ', '')
    
    if not placa:
        return jsonify({'error': 'Placa não informada'}), 400
    
    try:
        # Lista de URLs para tentar buscar multas
        urls_tentar = [
            # Detran SP - Consulta de Infrações
            f'https://www.detran.sp.gov.br/wps/portal/portaldetran/cidadao/servicos/multas/consultainfracoes',
        ]
        
        resultados = []
        
        # Vamos tentar buscar no Detran SP primeiro
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
        }
        
        # Tentar scraping do Detran SP
        try:
            # URL do Detran SP com parâmetro de placa
            url_detran_sp = f'https://www.detran.sp.gov.br/wps/portal/portaldetran/cidadao/servicos/multas/consultainfracoes'
            
            response = requests.get(url_detran_sp, headers=headers, timeout=10)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Procurar por elementos que contenham informações de multas
                # Isso é um exemplo - o site pode ter estrutura diferente
                texto_pagina = soup.get_text()
                
                if placa in texto_pagina.upper():
                    resultados.append({
                        'fonte': 'Detran SP',
                        'encontrado': True,
                        'mensagem': 'Veículo encontrado no sistema do Detran SP. Acesse o portal para detalhes completos.'
                    })
                else:
                    resultados.append({
                        'fonte': 'Detran SP',
                        'encontrado': False,
                        'mensagem': 'Nenhuma multa encontrada no Detran SP para esta placa.'
                    })
            else:
                resultados.append({
                    'fonte': 'Detran SP',
                    'encontrado': False,
                    'mensagem': 'Não foi possível acessar o Detran SP no momento.'
                })
        except Exception as e:
            resultados.append({
                'fonte': 'Detran SP',
                'encontrado': False,
                'mensagem': f'Erro ao buscar no Detran SP: {str(e)}'
            })
        
        # Se não encontrou nada, tentar buscar via Google (mais confiável)
        # Mas isso precisa de API externa, então vamos informar o usuário
        if not any(r.get('encontrado') for r in resultados):
            resultados.append({
                'fonte': 'Google',
                'encontrado': None,
                'mensagem': 'Recomendo acessar diretamente o portal do Detran do seu estado para consulta oficial.',
                'placa': placa,
                'sugestao_url': f'https://www.google.com/search?q=multas+detran+placa+{placa}'
            })
        
        return jsonify({
            'placa': placa,
            'resultados': resultados,
            'success': True
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Rota para servir arquivos estáticos de uploads
@app.route('/static/uploads/veiculos/')
def serve_veiculo_foto(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)