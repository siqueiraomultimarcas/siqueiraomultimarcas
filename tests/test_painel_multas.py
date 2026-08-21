# -*- coding: utf-8 -*-
"""Testes do painel de multas — roda SEM tocar no banco.

Substitui _db() por um cursor falso com multas fabricadas e confere se o
painel classifica prazo, responsabilidade, gravidade e dinheiro do jeito
que a operacao precisa ler.
"""
import sys, os, io, json
from contextlib import contextmanager
from datetime import date, timedelta

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, r'c:\Users\fabio.pepplow\Desktop\Projetos Antigravity\Siqueirão\siqueiraomultimarcas')

os.environ['DATABASE_URL'] = 'postgresql://x:x@127.0.0.1:1/none?connect_timeout=1'
os.environ.setdefault('SECRET_KEY', 'test-only')

import app as APP

FAILS = []


def check(nome, cond, detalhe=''):
    print('[%s] %s%s' % ('OK  ' if cond else 'FALHA', nome, (' — ' + str(detalhe)) if detalhe else ''))
    if not cond:
        FAILS.append(nome)


HOJE = date.today()
COLUNAS = ['id', 'veiculo_id', 'motorista_id', 'data_infracao', 'descricao',
           'valor', 'pontos', 'gravidade', 'status', 'tipo_notificacao',
           'data_limite_defesa', 'data_vencimento', 'data_indicacao',
           'data_emissao_na', 'data_emissao_np', 'numero_auto',
           'placa', 'marca', 'modelo', 'nome_motorista',
           'locatario_id', 'locatario_nome']


def multa(**kw):
    base = dict.fromkeys(COLUNAS)
    base.update({'id': kw.get('id', 1), 'veiculo_id': 1, 'valor': 195.23,
                 'pontos': 5, 'gravidade': 'Grave', 'status': 'pendente',
                 'tipo_notificacao': 'multa', 'descricao': 'INFRACAO GENERICA',
                 'placa': 'AAA1A11', 'marca': 'Fiat', 'modelo': 'Mobi',
                 'data_infracao': str(HOJE - timedelta(days=10))})
    base.update(kw)
    return [base[c] for c in COLUNAS]


class FakeCursor:
    def __init__(self, linhas):
        self.description = [(c,) for c in COLUNAS]
        self._linhas = linhas

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._linhas


def painel_com(linhas, meses=12):
    """Roda a view do painel contra as linhas fabricadas."""
    @contextmanager
    def fake_db():
        yield None, FakeCursor(linhas)

    original = APP._db
    APP._db = fake_db
    try:
        with APP.app.test_request_context('/api/multas/painel?meses=%s' % meses):
            resp = APP.painel_multas.__wrapped__()   # pula o @login_required
            return json.loads(resp.get_data(as_text=True))
    finally:
        APP._db = original


# ═══ 1. Semaforo de prazo ═══
print('\n== 1. SEMAFORO DE PRAZO ==')
casos = [
    (None, 'sem_prazo', 'multa ainda sem notificacao emitida'),
    (-5,   'vencido',   'prazo de defesa ja passou'),
    (0,    'critico',   'vence hoje'),
    (3,    'critico',   'limite da faixa critica'),
    (4,    'atencao',   'primeiro dia da faixa de atencao'),
    (10,   'atencao',   'limite do alerta (PRAZO_DEFESA_ALERTA)'),
    (11,   'ok',        'fora do alerta'),
]
for dias, esperado, desc in casos:
    obtido = APP._classe_prazo(dias)
    check('prazo %s dias -> %s' % (dias, esperado), obtido == esperado,
          desc if obtido == esperado else 'veio %s' % obtido)

# ═══ 2. Fase do processo ═══
print('\n== 2. FASE DO PROCESSO ==')
check('sem notificacao -> registrada',
      APP._fase_multa({}) == 'registrada')
check('so NA emitida -> aviso',
      APP._fase_multa({'data_emissao_na': '2026-05-28'}) == 'aviso')
check('NP emitida vence NA -> cobranca',
      APP._fase_multa({'data_emissao_na': '2026-05-28',
                       'data_emissao_np': '2026-08-07'}) == 'cobranca')

# ═══ 3. Quem paga a conta ═══
print('\n== 3. RESPONSABILIDADE ==')
linhas = [
    multa(id=1, motorista_id=7, nome_motorista='Joao', valor=100.00),          # ja vinculada
    multa(id=2, locatario_id=9, locatario_nome='Maria', valor=200.00),         # havia locacao
    multa(id=3, valor=300.00),                                                 # sem ninguem
]
d = painel_com(linhas)
check('repassavel soma vinculada + a vincular', d['financeiro']['repassavel'] == 300.00,
      d['financeiro']['repassavel'])
check('absorvido conta so a sem locacao', d['financeiro']['absorvido'] == 300.00,
      d['financeiro']['absorvido'])
check('aberto = repassavel + absorvido',
      round(d['financeiro']['repassavel'] + d['financeiro']['absorvido'], 2) == d['financeiro']['aberto'])
check('card "a vincular" so conta a com locacao', d['acoes']['a_vincular']['qtd'] == 1,
      d['acoes']['a_vincular'])
check('ids do card batem com a contagem',
      len(d['ids']['a_vincular']) == d['acoes']['a_vincular']['qtd'] and d['ids']['a_vincular'] == [2])
check('ids "absorvidas" batem', d['ids']['absorvidas'] == [3])

# ═══ 4. Multa paga sai das contas de acao ═══
print('\n== 4. MULTA PAGA ==')
d = painel_com([
    multa(id=1, status='pago', valor=150.00),
    multa(id=2, valor=250.00),
])
check('pago nao entra em aberto', d['financeiro']['aberto'] == 250.00, d['financeiro'])
check('pago entra em quitado', d['financeiro']['pago'] == 150.00)
check('total soma os dois', d['financeiro']['total'] == 400.00)
check('paga fora da fila de acao', d['ids']['aberto'] == [2])

# ═══ 5. Prazos criticos e vencidos ═══
print('\n== 5. FILA DE PRAZOS ==')
d = painel_com([
    multa(id=1, data_limite_defesa=str(HOJE - timedelta(days=2))),   # vencido
    multa(id=2, data_limite_defesa=str(HOJE + timedelta(days=1))),   # critico
    multa(id=3, data_limite_defesa=str(HOJE + timedelta(days=7))),   # atencao
    multa(id=4, data_limite_defesa=str(HOJE + timedelta(days=40))),  # ok
    multa(id=5),                                                     # sem prazo
])
check('critico conta vencido + ate 3 dias', d['acoes']['prazo_critico']['qtd'] == 2,
      d['acoes']['prazo_critico'])
check('destaca quantas ja venceram', d['acoes']['prazo_critico']['vencidas'] == 1)
check('atencao conta a faixa de 4 a 10 dias', d['acoes']['prazo_atencao']['qtd'] == 1)
check('fila ordena pelo que vence primeiro',
      [m['id'] for m in d['prazos']] == [1, 2, 3, 4], [m['id'] for m in d['prazos']])
check('multa sem prazo fica fora da fila', 5 not in [m['id'] for m in d['prazos']])
check('dias negativos para a vencida', d['prazos'][0]['dias'] == -2, d['prazos'][0]['dias'])

# ═══ 6. Gravidade — NIC nao se mistura ═══
print('\n== 6. GRAVIDADE E NIC ==')
d = painel_com([
    multa(id=1, gravidade='Grave'),
    multa(id=2, gravidade='Media'),
    multa(id=3, gravidade='Nao pontua (NIC)', tipo_notificacao='nic',
          descricao='MULTA, POR NAO IDENTIFICACAO DO CONDUTOR INFRATOR'),
    multa(id=4, gravidade=None),
])
check('NIC vira categoria propria', d['gravidade']['NIC'] == 1, d['gravidade'])
check('NIC nao cai em Outras', d['gravidade']['Outras'] == 1, d['gravidade'])
check('gravidades conhecidas contadas',
      d['gravidade']['Grave'] == 1 and d['gravidade']['Media'] == 1)
check('top_infracoes usa gravidade normalizada',
      all(i['gravidade'] in ('Leve', 'Media', 'Grave', 'Gravissima', 'NIC', 'Outras')
          for i in d['top_infracoes']),
      [i['gravidade'] for i in d['top_infracoes']])

# ═══ 7. Serie historica ═══
print('\n== 7. SERIE HISTORICA ==')
d = painel_com([multa(id=1, data_infracao=str(HOJE), valor=100.00),
                multa(id=2, data_infracao=str(HOJE), valor=50.00)], meses=6)
check('serie tem uma barra por mes pedido', len(d['evolucao']) == 6, len(d['evolucao']))
check('ultimo mes e o atual', d['evolucao'][-1]['mes'] == HOJE.strftime('%Y-%m'),
      d['evolucao'][-1]['mes'])
check('mes atual soma os valores', d['evolucao'][-1]['valor'] == 150.00)
check('mes atual conta as multas', d['evolucao'][-1]['qtd'] == 2)
check('meses sem multa ficam zerados',
      all(e['qtd'] == 0 for e in d['evolucao'][:-1]))
check('meses fora do limite sao rejeitados',
      len(painel_com([], meses=999)['evolucao']) == 36)

# ═══ 8. Rankings ═══
print('\n== 8. RANKINGS ==')
d = painel_com([
    multa(id=1, placa='AAA1A11', valor=100.00, pontos=5, gravidade='Grave'),
    multa(id=2, placa='AAA1A11', valor=100.00, pontos=4, gravidade='Media'),
    multa(id=3, placa='BBB2B22', valor=500.00, pontos=7, gravidade='Gravissima'),
    multa(id=4, placa='CCC3C33', motorista_id=7, nome_motorista='Joao',
          valor=80.00, pontos=3),
    multa(id=5, placa='CCC3C33', locatario_id=7, locatario_nome='Joao',
          valor=20.00, pontos=3),
])
top = {v['placa']: v for v in d['top_veiculos']}
check('placa com mais multas vem primeiro', d['top_veiculos'][0]['placa'] == 'AAA1A11',
      d['top_veiculos'][0]['placa'])
check('soma valor por placa', top['AAA1A11']['valor'] == 200.00)
check('soma pontos por placa', top['AAA1A11']['pontos'] == 9)
check('quebra por gravidade', top['AAA1A11']['gravidade']['Grave'] == 1
      and top['AAA1A11']['gravidade']['Media'] == 1)
cli = {c['nome']: c for c in d['top_clientes']}
check('cliente junta vinculada e a vincular', cli['Joao']['qtd'] == 2, cli.get('Joao'))
check('cliente acumula pontos', cli['Joao']['pontos'] == 6)
check('cliente marca quantas faltam vincular', cli['Joao']['a_vincular'] == 1)

# ═══ 9. Base vazia nao quebra ═══
print('\n== 9. SEM DADOS ==')
d = painel_com([])
check('painel responde com base vazia', d['financeiro']['total'] == 0)
check('nenhuma divisao por zero', d['financeiro']['aberto'] == 0 and d['prazos'] == [])
check('serie continua com 12 meses', len(d['evolucao']) == 12)

# ═══ 10. Deducao de gravidade pelo valor (CTB) ═══
print('\n== 10. TABELA CTB ==')
ctb = [(88.38, 'Leve', 3), (130.16, 'Media', 4), (195.23, 'Grave', 5),
       (293.47, 'Gravissima', 7), (390.46, 'Grave', 5), (1467.35, 'Gravissima', 7)]
for valor, grav, pts in ctb:
    g, p, _f = APP._gravidade_e_pontos(valor, 'INFRACAO QUALQUER')
    check('R$ %s -> %s / %s pts' % (valor, grav, pts), g == grav and p == pts,
          'veio %s / %s' % (g, p))
g, p, _f = APP._gravidade_e_pontos(390.46, 'MULTA, POR NAO IDENTIFICACAO DO CONDUTOR')
check('NIC nao pontua ninguem', p == 0 and 'NIC' in (g or ''), '%s / %s' % (g, p))

# === 11. Reprocessamento das multas ja cadastradas ===
print('\n== 11. SINCRONIA ATUALIZA O QUE FALTA ==')

# Multa importada crua: sem notificacao, sem prazo, sem gravidade
crua = {'id': 10, 'data_emissao_na': None, 'data_emissao_np': None,
        'data_limite_defesa': None, 'data_vencimento': None, 'gravidade': None,
        'pontos': 0, 'codigo_orgao': None, 'codigo_infracao': None,
        'numero_renainf': None, 'data_pagamento': None, 'status': 'pendente'}

# O que o SNE devolve semanas depois, com o aviso ja emitido
sne = {'data_emissao_na': '2026-08-28', 'data_limite_defesa': '2026-09-27',
       'data_vencimento': '2026-10-15', 'gravidade_ctb': 'Grave', 'pontos': 5,
       'codigo_orgao': '380370', 'codigo_infracao': '76331',
       'data_pagamento': None}

f = APP._campos_sne_faltantes(crua, sne, {'renainf': 123456})
check('traz a data do aviso', f.get('data_emissao_na') == '2026-08-28', f)
check('traz o prazo de defesa', f.get('data_limite_defesa') == '2026-09-27')
check('traz gravidade e pontos', f.get('gravidade') == 'Grave' and f.get('pontos') == 5)
check('traz o renainf', f.get('numero_renainf') == '123456')
check('nao inventa pagamento', 'data_pagamento' not in f and 'status' not in f)

# Rodar de novo, ja preenchida, nao deve mexer em nada
cheia = dict(crua, data_emissao_na='2026-08-28', data_limite_defesa='2026-09-27',
             data_vencimento='2026-10-15', gravidade='Grave', pontos=5,
             codigo_orgao='380370', codigo_infracao='76331', numero_renainf='123456')
check('segunda passada nao gera update', APP._campos_sne_faltantes(cheia, sne, {}) == {},
      APP._campos_sne_faltantes(cheia, sne, {}))

# Nunca sobrescreve o que o operador ajustou a mao
corrigida = dict(cheia, data_limite_defesa='2026-09-30', gravidade='Gravissima')
f = APP._campos_sne_faltantes(corrigida, sne, {})
check('nao sobrescreve prazo corrigido a mao', 'data_limite_defesa' not in f, f)
check('nao sobrescreve gravidade corrigida a mao', 'gravidade' not in f)

# Baixa oficial: o orgao registrou pagamento
pago_no_sne = dict(sne, data_pagamento='2026-09-10')
f = APP._campos_sne_faltantes(cheia, pago_no_sne, {})
check('baixa oficial marca como pago', f.get('status') == 'pago', f)
check('guarda a data do pagamento', f.get('data_pagamento') == '2026-09-10')

# Baixa manual local nao pode ser desfeita pela API
ja_pago = dict(cheia, status='pago', data_pagamento=date(2026, 9, 1))
check('multa ja paga localmente fica quieta',
      APP._campos_sne_faltantes(ja_pago, sne, {}) == {},
      APP._campos_sne_faltantes(ja_pago, sne, {}))

# O SQL montado usa so colunas do proprio codigo
f = APP._campos_sne_faltantes(crua, sne, {'renainf': 9})
sets = ', '.join('%s = %%s' % c for c in f)
check('SQL do update sai bem formado',
      sets.count('= %s') == len(f) and ';' not in sets and '--' not in sets, sets)

print('\n' + '=' * 62)
print('PAINEL DE MULTAS OK' if not FAILS else 'FALHAS: %s' % ', '.join(FAILS))
sys.exit(1 if FAILS else 0)
