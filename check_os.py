import psycopg2

conn = psycopg2.connect(
    host='pgsql.lsws.com.br',
    port='5433',
    database='lserp',
    user='aff_bi',
    password='Bi@2026#'
)
cur = conn.cursor()

# tabelas que начина com os_
tables = ['os', 'os_agenda', 'os_check', 'os_config', 'os_config_check', 'os_custos', 'os_equip', 'os_foto', 'os_hist', 'os_itens', 'os_motivo', 'os_parc']

print('Contagem de registros:')
for t in tables:
    cur.execute(f'SELECT COUNT(*) FROM pepplow."{t}"')
    cnt = cur.fetchone()[0]
    print(f'  {t}: {cnt} registros')

conn.close()