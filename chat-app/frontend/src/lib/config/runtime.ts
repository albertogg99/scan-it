import { env } from '$env/dynamic/public';

export const apiBase = () => env.PUBLIC_API_BASE_URL || `http://${location.hostname}:8000`;
export const issuer = () => env.PUBLIC_OIDC_ISSUER || `http://${location.hostname}:8080/realms/swiss-discover`;
export const clientId = () => env.PUBLIC_OIDC_CLIENT_ID || 'swiss-discover-web';