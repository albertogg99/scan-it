import type { Locale } from '$lib/i18n';
export type ClientGuide = { name: string; documentation: string; steps: Record<Locale, string[]>; cloud: boolean };
export const clientGuides: ClientGuide[] = [
  { name: 'ChatGPT', documentation: 'https://platform.openai.com/docs/guides/developer-mode', cloud: true, steps: {
    en: ['Check developer-mode availability for your plan and workspace.', 'Enable developer mode in ChatGPT settings and add a remote MCP connector using the endpoint URL.', 'Confirm authentication compatibility in the official guide before connecting.'],
    de: ['Prufen Sie, ob der Entwicklermodus fur Ihren Tarif verfugbar ist.', 'Aktivieren Sie den Entwicklermodus in ChatGPT und fugen Sie einen entfernten MCP-Connector mit der Endpunkt-URL hinzu.', 'Prufen Sie die Authentifizierung in der offiziellen Anleitung.'],
    fr: ['Verifiez la disponibilite du mode developpeur pour votre offre.', 'Activez le mode developpeur dans ChatGPT et ajoutez un connecteur MCP avec l URL du point de terminaison.', 'Verifiez la compatibilite de l authentification dans la documentation officielle.'],
    it: ['Verifica la disponibilita della modalita sviluppatore per il tuo piano.', 'Attiva la modalita sviluppatore in ChatGPT e aggiungi un connettore MCP remoto con l URL dell endpoint.', 'Verifica la compatibilita dell autenticazione nella guida ufficiale.'] } },
  { name: 'Claude', documentation: 'https://support.claude.com/en/articles/11175166-getting-started-with-custom-connectors-using-remote-mcp', cloud: true, steps: {
    en: ['Open Customize > Connectors > + > Add custom connector.', 'Enter the MCP endpoint URL and follow any required authorization flow.'],
    de: ['Offnen Sie Customize > Connectors > + > Add custom connector.', 'Geben Sie die MCP-Endpunkt-URL ein und folgen Sie gegebenenfalls der Autorisierung.'],
    fr: ['Ouvrez Customize > Connectors > + > Add custom connector.', 'Saisissez l URL du point de terminaison MCP et suivez l eventuelle procedure d autorisation.'],
    it: ['Apri Customize > Connectors > + > Add custom connector.', 'Inserisci l URL dell endpoint MCP e segui l eventuale procedura di autorizzazione.'] } },
  { name: 'Open WebUI', documentation: 'https://docs.openwebui.com/features/extensibility/mcp', cloud: false, steps: {
    en: ['As an administrator, open Settings > Admin > Integrations.', 'Add a connection, choose MCP (Streamable HTTP), and enter the endpoint URL.', 'Verify authentication compatibility in your deployment before enabling it.'],
    de: ['Offnen Sie als Administrator Settings > Admin > Integrations.', 'Erstellen Sie eine Verbindung vom Typ MCP (Streamable HTTP) mit der Endpunkt-URL.', 'Prufen Sie die Authentifizierung Ihrer Installation.'],
    fr: ['En tant qu administrateur, ouvrez Settings > Admin > Integrations.', 'Ajoutez une connexion MCP (Streamable HTTP) avec l URL du point de terminaison.', 'Verifiez l authentification de votre installation.'],
    it: ['Come amministratore apri Settings > Admin > Integrations.', 'Aggiungi una connessione MCP (Streamable HTTP) con l URL dell endpoint.', 'Verifica l autenticazione nella tua installazione.'] } },
  { name: 'Other', documentation: 'https://modelcontextprotocol.io/docs/learn/architecture', cloud: false, steps: {
    en: ['Choose a client that supports remote MCP Streamable HTTP.', 'Enter the endpoint URL and configure independent authentication.', 'Credentials held by this app are never available to external clients.'],
    de: ['Wahlen Sie einen Client mit MCP Streamable HTTP.', 'Geben Sie die Endpunkt-URL ein und richten Sie eigene Zugangsdaten ein.', 'Die Zugangsdaten dieser App sind fur externe Clients nicht verfugbar.'],
    fr: ['Choisissez un client compatible MCP Streamable HTTP.', 'Saisissez l URL du point de terminaison et configurez une authentification independante.', 'Les identifiants de cette application ne sont jamais fournis aux clients externes.'],
    it: ['Scegli un client che supporta MCP Streamable HTTP.', 'Inserisci l URL dell endpoint e configura credenziali indipendenti.', 'Le credenziali dell app non sono disponibili ai client esterni.'] } }
];