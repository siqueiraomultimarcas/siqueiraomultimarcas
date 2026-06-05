import psycopg2
cfg={"host":"pgsql.lsws.com.br","port":"5433","database":"lserp","user":"aff_bi","password":"Bi@2026#"}
conn=psycopg2.connect(**cfg)
cur=conn.cursor()
cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='pepplow' AND table_name='pedido_itens' ORDER BY ordinal_position")
for row in cur.fetchall():
    print(row)
cur.close()
conn.close()