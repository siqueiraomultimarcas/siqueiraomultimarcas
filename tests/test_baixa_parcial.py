# -*- coding: utf-8 -*-
"""Testa a baixa de contas a receber com pagamento PARCIAL e geração de residual.

Usa um banco fake em memória (cursor simulado) — não toca no Postgres
nem no Asaas. Nenhuma cobrança real é criada.
"""
import sys, os, json, re

sys.stdout.reconfigure(encoding='utf-8')
BASE = r'c:\Users\fabio.pepplow\Desktop\Projetos Antigravity\Siqueirão\siqueiraomultimarcas'
sys.path.insert(0, BASE)
os.environ['DATABASE_URL'] = 'postgresql://x:x@127.0.0.1:1/none?connect_timeout=1'
os.environ.setdefault('SECRET_KEY', 'test-only')

import app as APP
from datetime import date, timedelta
from contextlib import contextmanager

# Testa a regra de negócio, não o login — Flask-Login respeita esta flag
APP.app.config['LOGIN_DISABLED'] = True
APP.app.config['TESTING'] = True

FAILS = []
def check(nome, cond, detalhe=''):
    print(f"[{'OK  ' if cond else 'FALHA'}] {nome}" + (f' — {detalhe}' if detalhe else ''))
    if not cond: FAILS.append(nome)


# ────────────────────── Banco fake ──────────────────────
class FakeDB:
    """Simula o mínimo de SQL que o endpoint de baixa usa."""
    def __init__(self, valor_previsto=650.0):
        self.pagamentos = {1: {'valor_previsto': valor_previsto, 'locacao_id': 10,
                               'data_inicio': date(2026, 8, 3), 'data_fim': date(2026, 8, 9),
                               'status': 'pendente', 'valor_pago': 0, 'desconto': 0}}
        self.multas = {1: {'valor': 300.0, 'motorista_id': 7, 'descricao': 'Excesso de velocidade'}}
        self.avulsas = {1: {'valor': 200.0, 'cliente_id': 5, 'descricao': 'Lavagem'}}
        self.inseridos = []      # novas linhas (residual)
        self.updates = []        # updates aplicados
        self._last = None
        self._seq = 100

    def execute(self, sql, params=None):
        s = ' '.join(sql.split())
        p = params or ()
        if s.startswith('SELECT valor_previsto, locacao_id'):
            r = self.pagamentos.get(p[0])
            self._last = (r['valor_previsto'], r['locacao_id'], r['data_inicio'], r['data_fim']) if r else None
        elif s.startswith('SELECT valor, motorista_id, descricao FROM multas'):
            r = self.multas.get(p[0])
            self._last = (r['valor'], r['motorista_id'], r['descricao']) if r else None
        elif s.startswith('SELECT valor, cliente_id, descricao FROM cobrancas_avulsas'):
            r = self.avulsas.get(p[0])
            self._last = (r['valor'], r['cliente_id'], r['descricao']) if r else None
        elif s.startswith('SELECT motorista_id, descricao FROM multas'):
            r = self.multas.get(p[0]);  self._last = (r['motorista_id'], r['descricao'])
        elif 'COALESCE(MAX(semana_numero)' in s:
            self._last = (9,)
        elif s.startswith('UPDATE pagamentos_locacao'):
            self.updates.append(('pagamento', p))
            pid = p[-1]
            if "status='pendente'" in s:          # reversão: só o id vem em params
                self.pagamentos[pid].update(status='pendente', valor_pago=None, desconto=0)
            else:
                self.pagamentos[pid].update(status='pago', valor_pago=p[1], desconto=p[2])
        elif s.startswith('UPDATE multas'):
            self.updates.append(('multa', p))
        elif s.startswith('UPDATE cobrancas_avulsas'):
            self.updates.append(('avulsa', p))
        elif s.startswith('INSERT INTO pagamentos_locacao'):
            self._seq += 1
            self.inseridos.append({'tabela': 'pagamentos_locacao', 'locacao_id': p[0],
                                   'data_inicio': p[2], 'data_fim': p[3],
                                   'valor_previsto': p[4], 'observacao': p[5]})
            self._last = (self._seq,)
        elif s.startswith('INSERT INTO cobrancas_avulsas'):
            self._seq += 1
            self.inseridos.append({'tabela': 'cobrancas_avulsas', 'cliente_id': p[0],
                                   'descricao': p[1], 'valor': p[2],
                                   'data_vencimento': p[3], 'observacoes': p[4]})
            self._last = (self._seq,)
        else:
            self._last = (0,)

    def fetchone(self):
        return self._last


def baixar(payload, db):
    """Chama o endpoint real com o banco fake injetado."""
    @contextmanager
    def _fake_db():
        yield (None, db)
    orig = APP._db
    APP._db = _fake_db
    try:
        with APP.app.test_request_context('/api/contas-receber/baixa',
                                          method='PUT', json=payload):
            resp = APP.baixa_contas_receber()
    finally:
        APP._db = orig
    body, status = (resp if isinstance(resp, tuple) else (resp, 200))
    return json.loads(body.get_data(as_text=True)), status


HOJE = str(date.today())

# ═══════════ 1. Pagamento integral ═══════════
print('\n== 1. PAGAMENTO INTEGRAL (R$650 de R$650) ==')
db = FakeDB(650.0)
r, st = baixar({'tipo': 'contrato', 'id': 1, 'data_recebimento': HOJE}, db)
check('retorna 200', st == 200, f'status={st}')
check('valor devido = 650', r['valor_devido'] == 650.0)
check('recebido = 650 quando valor_recebido é omitido', r['valor_recebido'] == 650.0)
check('saldo zero', r['saldo'] == 0.0)
check('NÃO gera residual', r['residual_gerado'] is False)
check('nenhuma linha nova criada', len(db.inseridos) == 0)

# ═══════════ 2. Pagamento PARCIAL com residual ═══════════
print('\n== 2. PARCIAL COM RESIDUAL (recebeu R$400 de R$650) ==')
db = FakeDB(650.0)
r, st = baixar({'tipo': 'contrato', 'id': 1, 'valor_recebido': 400,
                'tratamento_saldo': 'residual', 'data_recebimento': HOJE}, db)
check('retorna 200', st == 200, f'status={st}')
check('saldo calculado = 250', r['saldo'] == 250.0, f"saldo={r['saldo']}")
check('residual foi gerado', r['residual_gerado'] is True)
check('exatamente 1 cobrança nova criada', len(db.inseridos) == 1, f'{len(db.inseridos)} criada(s)')
if db.inseridos:
    novo = db.inseridos[0]
    check('residual vai para pagamentos_locacao', novo['tabela'] == 'pagamentos_locacao')
    check('residual tem valor 250', novo['valor_previsto'] == 250.0, f"={novo['valor_previsto']}")
    check('residual mantém o mesmo contrato (locacao_id=10)', novo['locacao_id'] == 10)
    check('observação explica a origem', 'Residual' in (novo['observacao'] or ''))
    # Vencimento exibido = data_fim + 1 dia
    venc_calc = novo['data_fim'] + timedelta(days=1)
    esperado  = date.today() + timedelta(days=APP.PRAZO_RESIDUAL_DIAS)
    check(f'residual NÃO nasce vencido — vence em +{APP.PRAZO_RESIDUAL_DIAS} dias por padrão',
          venc_calc == esperado, f'vence {venc_calc}, esperado {esperado}')
    check('vencimento do residual retornado na resposta',
          r.get('vencimento_residual') == str(esperado), r.get('vencimento_residual'))
check('conta original marcada como paga com R$400',
      db.pagamentos[1]['status'] == 'pago' and db.pagamentos[1]['valor_pago'] == 400.0,
      f"pago={db.pagamentos[1]['valor_pago']}")
check('saldo NÃO virou desconto (desconto continua 0)',
      db.pagamentos[1]['desconto'] == 0, f"desconto={db.pagamentos[1]['desconto']}")

# ═══════════ 3. Parcial perdoando o saldo ═══════════
print('\n== 3. PARCIAL PERDOANDO O SALDO (recebeu R$400, perdoa R$250) ==')
db = FakeDB(650.0)
r, st = baixar({'tipo': 'contrato', 'id': 1, 'valor_recebido': 400,
                'tratamento_saldo': 'desconto',
                'justificativa_desconto': 'Cliente devolveu o carro 3 dias antes',
                'data_recebimento': HOJE}, db)
check('retorna 200', st == 200, f'status={st}')
check('NÃO gera residual', r['residual_gerado'] is False)
check('nenhuma cobrança nova', len(db.inseridos) == 0)
check('saldo virou desconto (250)', db.pagamentos[1]['desconto'] == 250.0,
      f"desconto={db.pagamentos[1]['desconto']}")
check('valor pago registrado = 400', db.pagamentos[1]['valor_pago'] == 400.0)

# ═══════════ 4. Perdoar exige justificativa ═══════════
print('\n== 4. VALIDAÇÕES ==')
db = FakeDB(650.0)
r, st = baixar({'tipo': 'contrato', 'id': 1, 'valor_recebido': 400,
                'tratamento_saldo': 'desconto'}, db)
check('perdoar sem justificativa é rejeitado (400)', st == 400, f'status={st}')
check('mensagem explica o motivo', 'ustificativa' in r.get('error', ''), r.get('error'))

db = FakeDB(650.0)
r, st = baixar({'tipo': 'contrato', 'id': 1, 'desconto': 100}, db)
check('desconto sem justificativa é rejeitado', st == 400, f'status={st}')

db = FakeDB(650.0)
r, st = baixar({'tipo': 'contrato', 'id': 1, 'valor_recebido': 900}, db)
check('receber mais que o devido é rejeitado', st == 400, f'status={st}')
check('erro informa o valor correto', '650' in r.get('error', ''), r.get('error'))

db = FakeDB(650.0)
r, st = baixar({'tipo': 'contrato', 'id': 1, 'valor_recebido': -50}, db)
check('valor negativo é rejeitado', st == 400, f'status={st}')

db = FakeDB(650.0)
r, st = baixar({'tipo': 'xpto', 'id': 1}, db)
check('tipo inválido é rejeitado', st == 400, f'status={st}')

db = FakeDB(650.0)
r, st = baixar({'tipo': 'contrato', 'id': 999}, db)
check('id inexistente retorna 404', st == 404, f'status={st}')

# ═══════════ 5. Desconto + parcial combinados ═══════════
print('\n== 5. DESCONTO + PARCIAL (R$650 - R$50 desc = R$600 devido; pagou R$350) ==')
db = FakeDB(650.0)
r, st = baixar({'tipo': 'contrato', 'id': 1, 'desconto': 50,
                'justificativa_desconto': 'Diária cortesia',
                'valor_recebido': 350, 'tratamento_saldo': 'residual',
                'data_recebimento': HOJE}, db)
check('devido = 600 (650 - 50 desconto)', r['valor_devido'] == 600.0, f"={r['valor_devido']}")
check('saldo = 250 (600 - 350)', r['saldo'] == 250.0, f"saldo={r['saldo']}")
check('residual de 250 criado', db.inseridos and db.inseridos[0]['valor_previsto'] == 250.0)
check('desconto original preservado (50)', db.pagamentos[1]['desconto'] == 50.0,
      f"desconto={db.pagamentos[1]['desconto']}")

# ═══════════ 6. Residual de multa vira cobrança avulsa ═══════════
print('\n== 6. RESIDUAL DE MULTA E DE AVULSA ==')
db = FakeDB()
r, st = baixar({'tipo': 'multa', 'id': 1, 'valor_recebido': 100,
                'tratamento_saldo': 'residual', 'data_recebimento': HOJE}, db)
check('multa: saldo 200 (300 - 100)', r['saldo'] == 200.0, f"saldo={r['saldo']}")
check('multa: residual criado como cobrança avulsa',
      db.inseridos and db.inseridos[0]['tabela'] == 'cobrancas_avulsas')
check('multa: residual vinculado ao motorista (id 7)',
      db.inseridos and db.inseridos[0]['cliente_id'] == 7)
check('multa: residual com valor 200', db.inseridos and db.inseridos[0]['valor'] == 200.0)

db = FakeDB()
r, st = baixar({'tipo': 'avulsa', 'id': 1, 'valor_recebido': 120,
                'tratamento_saldo': 'residual', 'data_recebimento': HOJE}, db)
check('avulsa: saldo 80 (200 - 120)', r['saldo'] == 80.0, f"saldo={r['saldo']}")
check('avulsa: residual vinculado ao cliente (id 5)',
      db.inseridos and db.inseridos[0]['cliente_id'] == 5)

# ═══════════ 7. Centavos ═══════════
print('\n== 7. PRECISÃO DE CENTAVOS ==')
db = FakeDB(333.33)
r, st = baixar({'tipo': 'contrato', 'id': 1, 'valor_recebido': 111.11,
                'tratamento_saldo': 'residual', 'data_recebimento': HOJE}, db)
check('saldo = 222.22 sem erro de ponto flutuante', r['saldo'] == 222.22, f"saldo={r['saldo']}")
check('residual criado com centavos exatos',
      db.inseridos and db.inseridos[0]['valor_previsto'] == 222.22)

db = FakeDB(100.0)
r, st = baixar({'tipo': 'contrato', 'id': 1, 'valor_recebido': 99.995,
                'data_recebimento': HOJE}, db)
check('diferença abaixo de 1 centavo NÃO gera residual', r['residual_gerado'] is False,
      f"saldo={r['saldo']}")

# ═══════════ 8. Vencimento do residual ═══════════
print('\n== 8. DATA DE VENCIMENTO DO RESIDUAL ==')
db = FakeDB(650.0)
r, st = baixar({'tipo': 'contrato', 'id': 1, 'valor_recebido': 400,
                'tratamento_saldo': 'residual', 'data_recebimento': HOJE,
                'vencimento_residual': '2026-09-15'}, db)
check('aceita vencimento informado pelo operador', st == 200, f'status={st}')
check('resposta confirma o vencimento escolhido', r.get('vencimento_residual') == '2026-09-15',
      r.get('vencimento_residual'))
if db.inseridos:
    venc = db.inseridos[0]['data_fim'] + timedelta(days=1)
    check('residual do contrato vence na data escolhida', str(venc) == '2026-09-15', f'={venc}')
    check('data_inicio <= data_fim (período coerente)',
          db.inseridos[0]['data_inicio'] <= db.inseridos[0]['data_fim'])

db = FakeDB(650.0)
r, st = baixar({'tipo': 'contrato', 'id': 1, 'valor_recebido': 400,
                'data_recebimento': HOJE, 'vencimento_residual': '2020-01-01'}, db)
check('vencimento anterior ao recebimento é rejeitado', st == 400, f'status={st}')

db = FakeDB(650.0)
r, st = baixar({'tipo': 'contrato', 'id': 1, 'valor_recebido': 400,
                'data_recebimento': HOJE, 'vencimento_residual': 'xx/yy'}, db)
check('data inválida é rejeitada', st == 400, f'status={st}')

db = FakeDB()
r, st = baixar({'tipo': 'multa', 'id': 1, 'valor_recebido': 100,
                'data_recebimento': HOJE, 'vencimento_residual': '2026-10-20'}, db)
check('residual de multa usa o vencimento escolhido',
      db.inseridos and str(db.inseridos[0].get('data_vencimento')) == '2026-10-20',
      str(db.inseridos[0].get('data_vencimento')) if db.inseridos else 'nada inserido')

# ═══════════ 9. Checklist usa a mesma regra ═══════════
print('\n== 9. CHECKLIST — parcial pelo mesmo caminho ==')

def toggle(pid, payload, db):
    @contextmanager
    def _fake_db():
        yield (None, db)
    orig = APP._db
    APP._db = _fake_db
    try:
        with APP.app.test_request_context(f'/api/checklist/{pid}/toggle',
                                          method='PUT', json=payload):
            resp = APP.toggle_checklist(pid)
    finally:
        APP._db = orig
    body, status = (resp if isinstance(resp, tuple) else (resp, 200))
    return json.loads(body.get_data(as_text=True)), status

db = FakeDB(650.0)
r, st = toggle(1, {'pago': True, 'data_pagamento': HOJE}, db)
check('marcar pago integral funciona', st == 200 and r['status'] == 'pago', f'status={st}')
check('integral não gera residual', not r.get('residual_gerado'))
check('valor_pago = valor previsto', db.pagamentos[1]['valor_pago'] == 650.0,
      f"={db.pagamentos[1]['valor_pago']}")

db = FakeDB(650.0)
r, st = toggle(1, {'pago': True, 'valor_recebido': 400, 'data_pagamento': HOJE,
                   'vencimento_residual': '2026-09-01'}, db)
check('checklist aceita pagamento parcial', st == 200, f'status={st}')
check('checklist gera residual de 250', r.get('saldo') == 250.0 and r.get('residual_gerado'),
      f"saldo={r.get('saldo')}")
check('residual do checklist alimenta contas a receber (pagamentos_locacao)',
      db.inseridos and db.inseridos[0]['tabela'] == 'pagamentos_locacao')
check('residual do checklist respeita o vencimento informado',
      r.get('vencimento_residual') == '2026-09-01', r.get('vencimento_residual'))

db = FakeDB(650.0)
r, st = toggle(1, {'pago': True, 'valor_recebido': 900}, db)
check('checklist rejeita valor maior que o devido', st == 400, f'status={st}')

db = FakeDB(650.0)
r, st = toggle(1, {'pago': False}, db)
check('desmarcar volta para pendente', st == 200 and r['status'] == 'pendente')

# ═══════════ Resumo ═══════════
print('\n' + '=' * 64)
if FAILS:
    print(f'{len(FAILS)} FALHA(S):')
    for f in FAILS: print('  •', f)
    sys.exit(1)
print('BAIXA PARCIAL E RESIDUAL — TODOS OS TESTES PASSARAM')
