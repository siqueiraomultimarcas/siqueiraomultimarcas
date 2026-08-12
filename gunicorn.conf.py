# Configuração do gunicorn lida em Python.
#
# Por que existe: passar --bind 0.0.0.0:$PORT na linha de comando só funciona
# quando um shell expande a variável. O Railway executa o start command sem
# shell, então o gunicorn recebia a string literal "$PORT" e abortava com
# "'$PORT' is not a valid port number". Lendo aqui, a porta vem do ambiente
# independente de como o processo é iniciado.
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
workers = int(os.environ.get('WEB_CONCURRENCY', '2'))

# init_db() roda no boot de cada worker; o timeout precisa cobrir isso com folga
timeout = 120
graceful_timeout = 30
keepalive = 5

accesslog = '-'          # stdout — aparece nos Deploy Logs do Railway
errorlog = '-'
loglevel = 'info'
