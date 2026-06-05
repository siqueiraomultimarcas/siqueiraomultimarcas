import psycopg2

conn = psycopg2.connect(
    host='pgsql.lsws.com.br',
    port='5433',
    database='lserp',
    user='aff_bi',
    password='Bi@2026#'
)
cur = conn.cursor()

# Verifica a tabela OC
cur.execute('SELECT COUNT(*) FROM pepplow."oc"')
cnt = cur.fetchone()[0]
print(f'Tabela oc: {cnt} registros')

if cnt > 0:
    # Pega colunas
    cur.execute('''
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_schema = 'pepplow' AND table_name = 'oc' 
        ORDER BY ordinal_position
    ''')
    cols = cur.fetchall()
    print(f'\nColunas da tabela oc ({len(cols)}):')
    for c in cols[:20]:
        print(f'  {c[0]}: {c[1]}')
    
    # Pega alguns dados
    cur.execute('SELECT id, id_cliente, titulo, status, dh_inc FROM pepplow."oc" ORDER BY dh_inc DESC LIMIT 5')
    rows = cur.fetchall()
    print('\nUltimos 5 registros:')
    for r in rows:
        print(f'  ID: {r[0]} | Cliente: {r[1]} | Titulo: {r[2]} | Status: {r[3]} | Data: {r[4]}')

conn.close()