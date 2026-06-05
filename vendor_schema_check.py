import psycopg2
cfg={"host":"pgsql.lsws.com.br","port":"5433","database":"lserp","user":"aff_bi","password":"Bi@2026#"}
conn=psycopg2.connect(**cfg)
cur=conn.cursor()
for table in ["pedido", "pedido_itens"]:
    print("TABLE", table)
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s AND column_name LIKE %s ORDER BY column_name", ("pepplow", table, "%vendedor%"))
    print("\n".join(r[0] for r in cur.fetchall()))
    print("---")
cur.close()
conn.close()
