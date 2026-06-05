import psycopg2

conn = psycopg2.connect(
    host='pgsql.lsws.com.br',
    port='5433',
    database='lserp',
    user='aff_bi',
    password='Bi@2026#'
)
cur = conn.cursor()

# Pega todas as tabelas do schema pepplow
cur.execute('''
    SELECT table_name 
    FROM information_schema.tables 
    WHERE table_schema = 'pepplow' 
    ORDER BY table_name
''')
all_tables = [t[0] for t in cur.fetchall()]

# Conta registros de cada tabela
print('Tabelas com dados (Top 30):')
count = 0
for t in all_tables:
    try:
        cur.execute(f'SELECT COUNT(*) FROM pepplow."{t}"')
        cnt = cur.fetchone()[0]
        if cnt > 0:
            print(f'  {t}: {cnt}')
            count += 1
            if count >= 30:
                break
    except:
        pass

print(f'\nTotal de tabelas com dados: {count}')
conn.close()