# -*- coding: utf-8 -*-
"""Testa o endpoint de comprovante de pagamento.
Usa banco fake em memória — não toca no Postgres nem no Asaas."""
import sys, os, json
sys.stdout.reconfigure(encoding='utf-8')
BASE = r'c:\Users\fabio.pepplow\Desktop\Projetos Antigravity\Siqueirão\siqueiraomultimarcas'
sys.path.insert(0, BASE)
os.environ['DATABASE_URL'] = 'postgresql://x:x@127.0.0.1:1/none?connect_timeout=1'
os.environ.setdefault('SECRET_KEY', 'test-only')

import app as APP
from datetime import date
from contextlib import contextmanager

APP.app.config['LOGIN_DISABLED'] = True
APP.app.config['TESTING'] = True

FAILS = []
def check(nome, cond, detalhe=''):
    print(f"[{'OK  ' if cond else 'FALHA'}] {nome}" + (f' — {detalhe}' if detalhe else ''))
    if not cond: FAILS.append(nome)


class FakeCur:
    """Devolve linhas conforme a query, imitando psycopg2 + row_to_dict."""
    def __init__(self, registro, residual=None):
        self.registro = registro
        self.residual = residual
        self._cols = []
        self._row = None

    def execute(self, sql, params=None):
        s = ' '.join(sql.split())
        if 'FROM pagamentos_locacao pl' in s and 'JOIN locacoes' in s:
            self._cols = ['id','data_inicio','data_fim','valor_previsto','valor_pago','desconto',
                          'data_pagamento','forma_pagamento','status','observacao',
                          'justificativa_desconto','nome','cpf','telefone','email',
                          'placa','marca','modelo','locacao_id']
            self._row = self.registro
        elif 'FROM multas m' in s:
            self._cols = ['id','valor_previsto','valor_pago','desconto','data_pagamento',
                          'forma_pagamento','status','observacao','justificativa_desconto',
                          'descricao','numero_auto','nome','cpf','telefone','email',
                          'placa','marca','modelo']
            self._row = self.registro
        elif 'FROM cobrancas_avulsas ca' in s:
            self._cols = ['id','valor_previsto','valor_pago','desconto','data_pagamento',
                          'forma_pagamento','status','observacao','justificativa_desconto',
                          'descricao','nome','cpf','telefone','email']
            self._row = self.registro
        elif "status = 'pendente'" in s and 'observacao LIKE' in s:
            self._cols = ['id','valor_previsto','vencimento']
            self._row = self.residual
        else:
            self._cols, self._row = [], None

    @property
    def description(self):
        return [(c,) for c in self._cols]

    def fetchone(self):
        return self._row


def pedir(tipo, rid, cur):
    @contextmanager
    def _fake_db():
        yield (None, cur)
    orig = APP._db
    APP._db = _fake_db
    try:
        with APP.app.test_request_context(f'/api/comprovante/{tipo}/{rid}'):
            resp = APP.get_comprovante(tipo, rid)
    finally:
        APP._db = orig
    body, status = (resp if isinstance(resp, tuple) else (resp, 200))
    return json.loads(body.get_data(as_text=True)), status


# ═══════════ 1. Contrato pago integralmente ═══════════
print('\n== 1. CONTRATO PAGO INTEGRALMENTE ==')
reg = (7, date(2026,8,3), date(2026,8,9), 650.0, 650.0, 0.0,
       date(2026,8,4), 'PIX', 'pago', None, None,
       'JOAO DA SILVA', '12345678900', '(41) 99888-7766', 'joao@x.com',
       'ABC1D23', 'Fiat', 'Argo', 10)
r, st = pedir('contrato', 7, FakeCur(reg))
check('retorna 200', st == 200, f'status={st}')
check('numero do recibo formatado', r['numero'] == 'CON-000007', r.get('numero'))
check('nome do cliente', r['cliente'] == 'JOAO DA SILVA')
check('telefone só com dígitos (pronto p/ wa.me)', r['telefone'] == '41998887766', r.get('telefone'))
check('valor recebido', r['valor_pago'] == 650.0)
check('data em formato brasileiro', r['data_pagamento'] == '04/08/2026', r.get('data_pagamento'))
check('período legível', r['periodo'] == '03/08/2026 a 09/08/2026', r.get('periodo'))
check('descrição cita a placa', 'ABC1D23' in r['descricao'], r.get('descricao'))
check('forma de pagamento', r['forma_pagamento'] == 'PIX')
check('sem residual quando integral', r['residual'] is None)

# ═══════════ 2. Contrato com pagamento parcial ═══════════
print('\n== 2. CONTRATO COM RESIDUAL ==')
reg_p = (8, date(2026,8,3), date(2026,8,9), 650.0, 400.0, 0.0,
         date(2026,8,4), 'Dinheiro', 'pago', None, None,
         'MARIA SOUZA', '98765432100', '41 97777-1122', '',
         'XYZ4E56', 'VW', 'Gol', 11)
res = (99, 250.0, date(2026,8,11))
r, st = pedir('contrato', 8, FakeCur(reg_p, residual=res))
check('retorna 200', st == 200, f'status={st}')
check('valor recebido parcial', r['valor_pago'] == 400.0)
check('residual presente', r['residual'] is not None)
check('valor do residual', r['residual'] and r['residual']['valor'] == 250.0,
      str(r.get('residual')))
check('vencimento do residual em dd/mm/aaaa',
      r['residual'] and r['residual']['vencimento'] == '11/08/2026',
      str(r.get('residual')))

# ═══════════ 3. Desconto ═══════════
print('\n== 3. COM DESCONTO ==')
reg_d = (9, date(2026,8,3), date(2026,8,9), 650.0, 600.0, 50.0,
         date(2026,8,4), 'PIX', 'pago', None, 'Carro ficou 1 dia na oficina',
         'CARLOS LIMA', '11122233344', '', '', 'QWE7F89', 'Chevrolet', 'Onix', 12)
r, st = pedir('contrato', 9, FakeCur(reg_d))
check('desconto exposto', r['desconto'] == 50.0)
check('justificativa exposta', 'oficina' in r['justificativa'], r.get('justificativa'))
check('sem telefone devolve string vazia', r['telefone'] == '', repr(r.get('telefone')))

# ═══════════ 4. Não emite comprovante de conta não paga ═══════════
print('\n== 4. VALIDAÇÕES ==')
reg_pend = list(reg); reg_pend[8] = 'pendente'
r, st = pedir('contrato', 7, FakeCur(tuple(reg_pend)))
check('conta pendente é rejeitada (400)', st == 400, f'status={st}')
check('mensagem explica o motivo', 'não foi paga' in r.get('error', '').lower(), r.get('error'))

r, st = pedir('contrato', 7, FakeCur(None))
check('id inexistente retorna 404', st == 404, f'status={st}')

r, st = pedir('xpto', 1, FakeCur(None))
check('tipo inválido retorna 400', st == 400, f'status={st}')

# ═══════════ 5. Multa e avulsa ═══════════
print('\n== 5. MULTA E AVULSA ==')
reg_m = (5, 300.0, 300.0, 0.0, date(2026,8,4), None, 'pago', None, None,
         'Excesso de velocidade', 'AIT-9911',
         'JOAO DA SILVA', '12345678900', '41999998888', '', 'ABC1D23', 'Fiat', 'Argo')
r, st = pedir('multa', 5, FakeCur(reg_m))
check('multa: retorna 200', st == 200, f'status={st}')
check('multa: numero com prefixo MUL', r['numero'] == 'MUL-000005', r.get('numero'))
check('multa: descrição cita a infração', 'velocidade' in r['descricao'], r.get('descricao'))
check('multa: referência traz placa e auto',
      'ABC1D23' in r['referencia'] and 'AIT-9911' in r['referencia'], r.get('referencia'))

reg_a = (3, 200.0, 200.0, 0.0, date(2026,8,4), None, 'recebido', None, None,
         'Lavagem completa', 'ANA PAULA', '55566677788', '41988887777', '')
r, st = pedir('avulsa', 3, FakeCur(reg_a))
check('avulsa: aceita status "recebido"', st == 200, f'status={st}')
check('avulsa: numero com prefixo AVU', r['numero'] == 'AVU-000003', r.get('numero'))
check('avulsa: descrição preservada', r['descricao'] == 'Lavagem completa', r.get('descricao'))

# ═══════════ 6. Helper de data ═══════════
print('\n== 6. FORMATAÇÃO DE DATA ==')
check("_fmt_br(date) -> dd/mm/aaaa", APP._fmt_br(date(2026,12,25)) == '25/12/2026')
check("_fmt_br('2026-01-05') -> 05/01/2026", APP._fmt_br('2026-01-05') == '05/01/2026')
check('_fmt_br(None) -> vazio', APP._fmt_br(None) == '')

print('\n' + '=' * 62)
if FAILS:
    print(f'{len(FAILS)} FALHA(S):')
    for f in FAILS: print('  •', f)
    sys.exit(1)
print('COMPROVANTE — TODOS OS TESTES PASSARAM')
