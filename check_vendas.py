import psycopg2

conn = psycopg2.connect(
    host='pgsql.lsws.com.br',
    port='5433',
    database='lserp',
    user='aff_bi',
    password='Bi@2026#'
)
cur = conn.cursor()

# Verifica tabelas de venda/comissão
tabelas = ['comissao', 'comissao_itens', 'pedido', 'orcamento', 'orcamento_vend', 'pedido_vend', 'filial_vendedor']

print('Contagem de registros:')
for t in tabelas:
    try:
        cur.execute(f'SELECT COUNT(*) FROM pepplow."{t}"')
        cnt = cur.fetchone()[0]
        print(f'  {t}: {cnt}')
    except:
        print(f'  {t}: ERRO')

# Verifica colunas de comissao
print('\nColunas da tabela comissao:')
cur.execute('''
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_schema = 'pepplow' AND table_name = 'comissao' 
    ORDER BY ordinal_position
''')
for c in cur.fetchall():
    print(f'  {c[0]}: {c[1]}')

# Verifica algumas colunas de pedido
print('\nColunas da tabela pedido:')
cur.execute('''
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_schema = 'pepplow' AND table_name = 'pedido' 
    ORDER BY ordinal_position
    LIMIT 30
''')
for c in cur.fetchall():
    print(f'  {c[0]}: {c[1]}')

conn.close()