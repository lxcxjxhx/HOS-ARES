"""Find python-build-standalone with Python 3.12+."""
import urllib.request, ssl, json

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# Get all releases, look for Python 3.12
page = 1
found = []
while page <= 10:
    url = f'https://api.github.com/repos/indygreg/python-build-standalone/releases?per_page=30&page={page}'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
        resp = urllib.request.urlopen(req, context=ctx, timeout=15)
        data = json.loads(resp.read())
        if not data:
            break
        for release in data:
            tag = release.get('tag_name', '')
            assets = release.get('assets', [])
            for a in assets:
                name = a.get('name', '')
                if 'aarch64-unknown-linux-musl' in name and 'install_only' in name and name.endswith('.tar.gz'):
                    found.append((tag, name, a['browser_download_url'], a['size']))
        page += 1
    except Exception as e:
        print(f'Error: {e}')
        break

# Show Python 3.12+ builds
print("aarch64-musl install_only builds:")
for tag, name, url, size in found:
    py_match = __import__('re').search(r'cpython-(\d+\.\d+\.\d+)', name)
    if py_match:
        py_ver = py_match.group(1)
        if py_ver.startswith('3.1') or py_ver.startswith('3.2'):
            print(f'  Python {py_ver} | {tag} | {size//1024//1024} MB')
            print(f'    {url}')

# Also show latest available
print("\nLatest 3 releases:")
for tag, name, url, size in found[:3]:
    print(f'  {tag} | {name}')