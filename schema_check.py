import psycopg2
cfg = {"host":"pgsql.lsws.com.br","port":"5433","database":"lserp","user":"aff_bi","password":"Bi@2026#"}
conn = psycopg2.connect(**cfg)
cur = conn.cursor()
for table in ['pedido', 'pedido_itens']:
    cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='pepplow' AND table_name=%s ORDER BY ordinal_position", (table,))
    print('TABLE', table)
    for row in cur.fetchall():
        print(row)
    print()
cur.close()
conn.close()
