import sqlite3
conn = sqlite3.connect('locadora.db')
conn.execute('ALTER TABLE veiculos ADD COLUMN foto TEXT')
conn.commit()
conn.close()
print('Coluna foto adicionada!')
