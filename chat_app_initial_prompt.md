I want to create a llm chat app that can be configured from a url to load a specific mcp per chat session, that way a user could scan a qr code or click a button on a web and the chat directly connects to the linked mcp by id and start chatting with it, with prompts, tools and so on connected to real time or not systems, for things like museums  bus stops, and more disconnected things like parkings and other application. The main thing is that is should be mcp agnostic, just a chat interface, with proper streaming, in chat tool call display, reasoining display and so on, with a special flow were the "apps" main prompt get loaded at the beginning to give it context and start using the tool on the users command. 

The chat interface, when entering it, should have two modes, one were you can receive intructions on how to configure said mcp with popular chat applications, open web ui, claude, chatgpt and so on. and another mode were you start chatting directly inside our app. The model we are going to use is Apertus 1.5 70B, configure the app for that although it should be openai compatible. I would add the api key to configure it later. Leave the model configurable, but the default is apertus, search also what the reasoning and tool call tokens / type is because streaming should be well parsed.

For the agent/mcp connection in the backend with the actual llm, discuss with me and give me options of what we should use, if langchain, fastmcp client or another option

The flow arribing to the ui should be Local Chat / Configure Other -> if configure other -> Chatgpt / claude /openwebui / other . If local chat -> login -> open the chat application directly. 

The deployment stack should be docker compose, the login a simple keycloak with a custom login screen with a custom desing.

Also you should think about an overall style of the frontend and login prompt according to the challenge, this is a challenge in the zurich ai hackathon today for Swiss Public AI, so keep the overall theme swiss minded and similar to their website here https://publicai.co/chat , keep the overall ui minimalistic and modern

To store the conversations for each user use a simple docker postgres container


The frontend should be done with svelte, and the backend in python fast api. Make a super simple mcp with fastmcp just for testing too.

Refine this prompt first and we will then go into implementation

Be specific and super clear to the sub agents on what to do


# Further instructions 

Use web search for any info that you need
Ask me questions using the tool about anything im missing, desing decision or to resolve issues.
Use the strong implementation sub agent for the localized implementation steps, one sub agent per step, in parallel if possible
