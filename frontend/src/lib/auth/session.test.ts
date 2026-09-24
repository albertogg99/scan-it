import { afterEach, describe, expect, it, vi } from 'vitest';
import { KJUR, KEYUTIL } from 'jsrsasign';
import { accessToken, finishLogin, login, pkceChallenge, safeReturn } from './session';

afterEach(() => vi.unstubAllGlobals());

describe('OIDC redirect and PKCE', () => {
  it('rejects external and protocol-relative redirects', () => {
    expect(safeReturn('/m/museum-guide/chat?conversation=abc')).toBe('/m/museum-guide/chat?conversation=abc');
    expect(safeReturn('//external.example')).toBe('/');
    expect(safeReturn('https://external.example')).toBe('/');
    expect(safeReturn('/\\external.example')).toBe('/');
  });
  it('computes RFC 7636 S256 without SubtleCrypto', () => {
    expect(pkceChallenge('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk')).toBe('E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM');
  });
  it('verifies state, nonce, issuer, signature and stores a valid session on HTTP LAN', async () => {
    const storage = new Map<string, string>();
    vi.stubGlobal('sessionStorage', { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value), removeItem: (key: string) => storage.delete(key) });
    const assign = vi.fn();
    vi.stubGlobal('location', { hostname: 'localhost', origin: 'http://localhost:3000', assign });
    const issuer = 'http://localhost:8080/realms/swiss-discover';
    const keys = KEYUTIL.generateKeypair('RSA', 1024);
    const jwk = { ...KEYUTIL.getJWK(keys.pubKeyObj), kid: 'test', use: 'sig', alg: 'RS256' };
    let nonce = '';
    const fetchMock = vi.fn(async (input: string) => {
      if (input.endsWith('/.well-known/openid-configuration')) return Response.json({ issuer, authorization_endpoint: `${issuer}/auth`, token_endpoint: `${issuer}/token`, jwks_uri: `${issuer}/keys` });
      if (input.endsWith('/keys')) return Response.json({ keys: [jwk] });
      const id_token = KJUR.jws.JWS.sign('RS256', JSON.stringify({ alg: 'RS256', kid: 'test' }), { iss: issuer, aud: 'swiss-discover-web', sub: 'user', exp: Math.floor(Date.now() / 1000) + 300, iat: Math.floor(Date.now() / 1000), nonce }, keys.prvKeyObj);
      return Response.json({ access_token: 'access', refresh_token: 'refresh', id_token, expires_in: 300 });
    });
    vi.stubGlobal('fetch', fetchMock);
    await login('/m/museum-guide/chat?conversation=abc');
    const params = new URL(assign.mock.calls[0][0] as string).searchParams;
    nonce = params.get('nonce')!;
    await expect(finishLogin(new URLSearchParams({ state: 'forged', code: 'valid' }))).rejects.toThrow('OIDC response invalid or expired');
    await expect(finishLogin(new URLSearchParams({ state: params.get('state')!, code: 'valid' }))).resolves.toBe('/m/museum-guide/chat?conversation=abc');
    expect(await accessToken()).toBe('access');
    expect(storage.get('sd.tokens')).not.toContain('verifier');
    await login('/m/zurich-transit/chat');
    const next = new URL(assign.mock.calls[1][0] as string).searchParams;
    nonce = 'forged';
    await expect(finishLogin(new URLSearchParams({ state: next.get('state')!, code: 'valid' }))).rejects.toThrow('nonce');
  });
});