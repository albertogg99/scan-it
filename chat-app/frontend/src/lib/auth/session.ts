import { sha256 } from '@noble/hashes/sha256';
import type { RSAKey } from 'jsrsasign';
import { clientId, issuer } from '$lib/config/runtime';

type Tokens = { access_token: string; refresh_token?: string; id_token: string; expires_at: number };
type Transaction = { verifier: string; nonce: string; returnTo: string };
type Discovery = { issuer: string; authorization_endpoint: string; token_endpoint: string; jwks_uri: string; end_session_endpoint?: string };
const tokenKey = 'sd.tokens';
const pendingPrefix = 'sd.pending.';
let refreshPromise: Promise<string | null> | null = null;
let discoveryPromise: Promise<Discovery> | null = null;
const encode = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const random = () => encode(crypto.getRandomValues(new Uint8Array(32)));
const callback = () => `${location.origin}/auth/callback`;
export const safeReturn = (value: string) => value.startsWith('/') && !value.startsWith('//') && !value.includes('\\') ? value : '/';
export const pkceChallenge = (verifier: string) => encode(sha256(new TextEncoder().encode(verifier)));
const tokens = (): Tokens | null => {
  try { return JSON.parse(sessionStorage.getItem(tokenKey) || 'null') as Tokens | null; } catch { return null; }
};
export function clearSession() { sessionStorage.removeItem(tokenKey); }
async function discovery(): Promise<Discovery> {
  discoveryPromise ??= (async () => {
    const response = await fetch(`${issuer()}/.well-known/openid-configuration`);
    if (!response.ok) throw new Error('OIDC discovery unavailable');
    const doc = await response.json() as Discovery;
    if (doc.issuer !== issuer() || [doc.authorization_endpoint, doc.token_endpoint, doc.jwks_uri].some(url => new URL(url).origin !== new URL(issuer()).origin)) throw new Error('OIDC issuer mismatch');
    return doc;
  })().catch(error => { discoveryPromise = null; throw error; });
  return discoveryPromise;
}
async function verify(idToken: string, nonce?: string) {
  const [doc, { KJUR, KEYUTIL }] = await Promise.all([discovery(), import('jsrsasign')]);
  const jwksResponse = await fetch(doc.jwks_uri, { cache: 'no-store' });
  if (!jwksResponse.ok) throw new Error('OIDC keys unavailable');
  const jwks = await jwksResponse.json() as { keys: { kid?: string; kty?: string; use?: string; alg?: string; n?: string; e?: string }[] };
  const [headerRaw, payloadRaw] = idToken.split('.');
  if (!headerRaw || !payloadRaw) throw new Error('Invalid ID token');
  const decode = (raw: string) => JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(raw.replace(/-/g, '+').replace(/_/g, '/')), char => char.charCodeAt(0)))) as Record<string, unknown>;
  const header = decode(headerRaw);
  const payload = decode(payloadRaw);
  const jwk = jwks.keys.find(key => key.kid === header.kid && key.kty === 'RSA' && key.use === 'sig' && (!key.alg || key.alg === 'RS256'));
  if (header.alg !== 'RS256' || !jwk?.n || !jwk.e || !KJUR.jws.JWS.verifyJWT(idToken, KEYUTIL.getKey(jwk as unknown as Parameters<typeof KEYUTIL.getKey>[0]) as RSAKey, { alg: ['RS256'], iss: [issuer()], aud: [clientId()], gracePeriod: 60 })) throw new Error('Invalid ID token');
  if (typeof payload.exp !== 'number' || payload.exp <= Date.now() / 1000 || typeof payload.iat !== 'number' || payload.iat > Date.now() / 1000 + 60) throw new Error('Expired or invalid ID token');
  if (nonce !== undefined && payload.nonce !== nonce) throw new Error('OIDC nonce mismatch');
}
export async function login(returnTo: string) {
  const doc = await discovery();
  const verifier = random();
  const state = random();
  const nonce = random();
  sessionStorage.setItem(pendingPrefix + state, JSON.stringify({ verifier, nonce, returnTo: safeReturn(returnTo) } satisfies Transaction));
  const url = new URL(doc.authorization_endpoint);
  url.search = new URLSearchParams({ client_id: clientId(), redirect_uri: callback(), response_type: 'code', scope: 'openid profile', state, nonce, code_challenge_method: 'S256', code_challenge: pkceChallenge(verifier) }).toString();
  location.assign(url.href);
}
export function pendingReturnTo(params: URLSearchParams): string {
  try { return safeReturn((JSON.parse(sessionStorage.getItem(pendingPrefix + params.get('state')) || 'null') as Transaction | null)?.returnTo ?? '/'); } catch { return '/'; }
}
export async function finishLogin(params: URLSearchParams): Promise<string> {
  const state = params.get('state');
  const code = params.get('code');
  if (!state) throw new Error('OIDC state missing');
  const raw = sessionStorage.getItem(pendingPrefix + state);
  sessionStorage.removeItem(pendingPrefix + state);
  if (!raw || !code || params.has('error')) throw new Error('OIDC response invalid or expired');
  const { verifier, nonce, returnTo } = JSON.parse(raw) as Transaction;
  const doc = await discovery();
  const response = await fetch(doc.token_endpoint, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({ grant_type: 'authorization_code', client_id: clientId(), redirect_uri: callback(), code, code_verifier: verifier }) });
  if (!response.ok) throw new Error('OIDC token exchange failed');
  const result = await response.json() as Tokens & { expires_in: number };
  await verify(result.id_token, nonce);
  sessionStorage.setItem(tokenKey, JSON.stringify({ access_token: result.access_token, refresh_token: result.refresh_token, id_token: result.id_token, expires_at: Date.now() + result.expires_in * 1000 } satisfies Tokens));
  return safeReturn(returnTo);
}
export async function accessToken(): Promise<string | null> {
  const current = tokens();
  if (!current) return null;
  if (current.expires_at > Date.now() + 30000) return current.access_token;
  if (!current.refresh_token) { clearSession(); return null; }
  refreshPromise ??= (async () => {
    try {
      const doc = await discovery();
      const response = await fetch(doc.token_endpoint, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({ grant_type: 'refresh_token', client_id: clientId(), refresh_token: current.refresh_token! }) });
      if (!response.ok) throw new Error('Refresh failed');
      const result = await response.json() as Tokens & { expires_in: number };
      await verify(result.id_token);
      sessionStorage.setItem(tokenKey, JSON.stringify({ access_token: result.access_token, refresh_token: result.refresh_token || current.refresh_token, id_token: result.id_token, expires_at: Date.now() + result.expires_in * 1000 } satisfies Tokens));
      return result.access_token;
    } catch { clearSession(); return null; }
  })().finally(() => { refreshPromise = null; });
  return refreshPromise;
}
export async function logout() {
  const current = tokens();
  clearSession();
  try {
    const doc = await discovery();
    if (doc.end_session_endpoint && new URL(doc.end_session_endpoint).origin === new URL(issuer()).origin) {
      const url = new URL(doc.end_session_endpoint);
      url.searchParams.set('client_id', clientId());
      url.searchParams.set('post_logout_redirect_uri', location.origin + '/');
      if (current?.id_token) url.searchParams.set('id_token_hint', current.id_token);
      location.assign(url.href);
      return;
    }
  } catch { clearSession(); }
  location.assign('/');
}