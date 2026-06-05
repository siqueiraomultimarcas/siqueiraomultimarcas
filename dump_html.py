import urllib.request
html=urllib.request.urlopen("http://127.0.0.1:5000/dashboard-vendas").read().decode("utf-8", errors='replace')
print(html[:1200])
print('---snip---')
print('filtrarDados' in html)
print('window.onload' in html)
