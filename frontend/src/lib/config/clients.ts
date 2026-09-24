import type { Locale } from '$lib/i18n';
export type ClientGuide = { name: string; documentation: string; steps: Record<Locale, string[]>; cloud: boolean };
export const clientGuides: ClientGuide[] = [
  { name: 'ChatGPT', documentation: 'https://platform.openai.com/docs/guides/developer-mode', cloud: true, steps: {
    en: ['Check developer-mode availability for your plan and workspace.', 'Enable developer mode in ChatGPT settings and add a remote MCP connector with a public HTTPS URL.', 'Confirm authentication compatibility in the official guide before connecting.'],
    de: ['Prufen Sie, ob der Entwicklermodus fur Ihren Tarif verfugbar ist.', 'Aktivieren Sie den Entwicklermodus in ChatGPT und fugen Sie einen entfernten MCP-Connector mit offentlicher HTTPS-Adresse hinzu.', 'Prufen Sie die Authentifizierung in der offiziellen Anleitung.'],
    fr: ['Verifiez la disponibilite du mode developpeur pour votre offre.', 'Activez le mode developpeur dans ChatGPT et ajoutez un connecteur MCP avec une adresse HTTPS publique.', 'Verifiez la compatibilite de l authentification dans la documentation officielle.'],
    it: ['Verifica la disponibilita della modalita sviluppatore per il tuo piano.', 'Attiva la modalita sviluppatore in ChatGPT e aggiungi un connettore MCP remoto con URL HTTPS pubblico.', 'Verifica la compatibilita dell autenticazione nella guida ufficiale.'] } },
  { name: 'Claude', documentation: 'https://support.claude.com/en/articles/11175166-getting-started-with-custom-connectors-using-remote-mcp', cloud: true, steps: {
    en: ['Open Customize > Connectors > + > Add custom connector.', 'Enter the public HTTPS remote MCP URL and follow any required authorization flow.', 'Claude connects from Anthropic cloud; local and LAN endpoints cannot be reached.'],
    de: ['Offnen Sie Customize > Connectors > + > Add custom connector.', 'Geben Sie die offentliche HTTPS-Adresse ein und folgen Sie gegebenenfalls der Autorisierung.', 'Claude verbindet sich aus der Anthropic-Cloud, nicht aus Ihrem LAN.'],
    fr: ['Ouvrez Customize > Connectors > + > Add custom connector.', 'Saisissez l adresse HTTPS publique et suivez l eventuelle procedure d autorisation.', 'Claude se connecte depuis le cloud Anthropic, pas depuis votre reseau local.'],
    it: ['Apri Customize > Connectors > + > Add custom connector.', 'Inserisci l URL HTTPS pubblico e segui l eventuale procedura di autorizzazione.', 'Claude si connette dal cloud Anthropic, non dalla rete locale.'] } },
  { name: 'Open WebUI', documentation: 'https://docs.openwebui.com/features/extensibility/mcp', cloud: false, steps: {
    en: ['As an administrator, open Settings > Admin > Integrations.', 'Add a connection, choose MCP (Streamable HTTP), and enter the endpoint reachable by your Open WebUI host.', 'Verify authentication compatibility in your deployment before enabling it.'],
    de: ['Offnen Sie als Administrator Settings > Admin > Integrations.', 'Erstellen Sie eine Verbindung vom Typ MCP (Streamable HTTP) mit einer von Open WebUI erreichbaren Adresse.', 'Prufen Sie die Authentifizierung Ihrer Installation.'],
    fr: ['En tant qu administrateur, ouvrez Settings > Admin > Integrations.', 'Ajoutez une connexion MCP (Streamable HTTP) avec une adresse accessible depuis Open WebUI.', 'Verifiez l authentification de votre installation.'],
    it: ['Come amministratore apri Settings > Admin > Integrations.', 'Aggiungi una connessione MCP (Streamable HTTP) raggiungibile dal server Open WebUI.', 'Verifica l autenticazione nella tua installazione.'] } },
  { name: 'Other', documentation: 'https://modelcontextprotocol.io/docs/learn/architecture', cloud: false, steps: {
    en: ['Choose a client that supports remote MCP Streamable HTTP.', 'Make sure its host can reach the endpoint and configure independent authentication.', 'Credentials held by this app are never available to external clients.'],
    de: ['Wahlen Sie einen Client mit MCP Streamable HTTP.', 'Prufen Sie die Erreichbarkeit und richten Sie eigene Zugangsdaten ein.', 'Die Zugangsdaten dieser App sind fur externe Clients nicht verfugbar.'],
    fr: ['Choisissez un client compatible MCP Streamable HTTP.', 'Verifiez l acces a l adresse et configurez une authentification independante.', 'Les identifiants de cette application ne sont jamais fournis aux clients externes.'],
    it: ['Scegli un client che supporta MCP Streamable HTTP.', 'Verifica la raggiungibilita e configura credenziali indipendenti.', 'Le credenziali dell app non sono disponibili ai client esterni.'] } }
];