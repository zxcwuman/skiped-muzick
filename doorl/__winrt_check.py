import json
import urllib.request

url = 'https://pypi.org/pypi/winrt/json'
with urllib.request.urlopen(url) as r:
    data = json.load(r)
print('version:', data['info']['version'])
for f in data['releases']['1.0.21033.1']:
    print(f['filename'], f['python_version'], f['packagetype'])
