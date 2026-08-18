import urllib.request, ssl, re, sys

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

urls = [
    'https://dl-cdn.alpinelinux.org/alpine/v3.20/packages/aarch64/',
    'https://dl-cdn.alpinelinux.org/alpine/v3.20/packages/main/aarch64/',
    'https://dl-cdn.alpinelinux.org/alpine/latest-stable/packages/aarch64/',
    'https://dl-cdn.alpinelinux.org/alpine/latest-stable/packages/main/aarch64/',
]
for url in urls:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, context=ctx, timeout=15)
        print(f'OK: {url} -> {resp.status}')
        content = resp.read().decode('utf-8', errors='ignore')
        matches = re.findall(r'href="([^"]*python3[^"]*\.apk)"', content)
        if matches:
            print(f'  Found {len(matches)} python3 packages:')
            for m in matches[:5]:
                print(f'    {m}')
            break
        else:
            all_apk = re.findall(r'href="([^"]*\.apk)"', content)
            print(f'  No python3 packages, but found {len(all_apk)} .apk files')
            if all_apk:
                print(f'  First few: {all_apk[:5]}')
            print(f'  Preview: {content[:500]}')
    except Exception as e:
        print(f'FAIL: {url} -> {e}')