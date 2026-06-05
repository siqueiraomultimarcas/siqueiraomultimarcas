import psycopg2
from flask import Flask, jsonify, cors
import json

app = Flask(__name__)

# Configurações do banco
DB_CONFIG = {
    'host': 'pgsql.lsws.com.br',
    'port': '5433',
    'database': 'lserp',
    'user': 'aff_bi',
    'password': 'Bi@2026#'
}

# Tabelas importantes para KPIs
TABELAS_IMPORTANTES = [
    'cliente', 'cliente_veic', 'colaborador', 'conta_movimento', 'caixa', 'caixa_lanc', 
    'caixa_fecha', 'estoque', 'item', 'item_estoque', 'estoque_movimento',
    'alerta', 'boleto', 'boleto_hist', 'cartao_ponto', 'colaborador_ponto',
    'veiculo', 'veic_marca', 'veic_modelo', 'cliente_agenda', 'cliente_pontos',
    'configuracao', 'conta', 'cfop', 'forma', 'prazo_pgto'
]

def get_connection():
    return psycopg2.connect(**DB_CONFIG)

@app.route('/api/kpis', methods=['GET'])
def get_kpis():
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        kpis = {'tabelasComDados': {}, 'totalTabelas': 0, 'totalViews': 0}
        
        # Conta total de tabelas
        cur.execute("""
            SELECT COUNT(*) 
            FROM information_schema.tables 
            WHERE table_schema = 'pepplow' AND table_type = 'BASE TABLE'
        """)
        kpis['totalTabelas'] = cur.fetchone()[0]
        
        # Conta views
        cur.execute("""
            SELECT COUNT(*) 
            FROM information_schema.views 
            WHERE table_schema = 'pepplow'
        """)
        kpis['totalViews'] = cur.fetchone()[0]
        
        # Pega contagem das tabelas importantes
        for tabela in TABELAS_IMPORTANTES:
            try:
                cur.execute(f'SELECT COUNT(*) FROM pepplow."{tabela}"')
                count = cur.fetchone()[0]
                if count > 0:
                    kpis['tabelasComDados'][tabela] = count
                    kpis[tabela] = {'total': count}
            except Exception as e:
                pass
        
        # Pega mais tabelas com dados
        cur.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'pepplow' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        all_tables = [t[0] for t in cur.fetchall()]
        
        for tabela in all_tables:
            if tabela not in kpis['tabelasComDados']:
                try:
                    cur.execute(f'SELECT COUNT(*) FROM pepplow."{tabela}"')
                    count = cur.fetchone()[0]
                    if count > 0:
                        kpis['tabelasComDados'][tabela] = count
                except:
                    pass
        
        cur.close()
        conn.close()
        
        return jsonify(kpis)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500)

@app.route('/api/tabelas', methods=['GET'])
def get_tabelas():
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'pepplow' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        tabelas = [t[0] for t in cur.fetchall()]
        
        result = []
        for tabela in tabelas[:100]:  # Limita a 100 para performance
            try:
                cur.execute(f'SELECT COUNT(*) FROM pepplow."{tabela}"')
                count = cur.fetchone()[0]
                result.append({'nome': tabela, 'registros': count})
            except:
                result.append({'nome': tabela, 'registros': 0})
        
        cur.close()
        conn.close()
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("=" * 50)
    print("Servidor API LSERP iniciado!")
    print("Acesse: http://localhost:5000/api/kpis")
    print("=" * 50)
    app.run(debug=True, port=5000)