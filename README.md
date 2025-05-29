# PartSelect ChatBot

Agentic AI framework developed to assist users with appliance information queries and troubleshooting guidelines. Integrated with both OpenAI and Deepseek.

### Setting Up
Install required packages
```
pip install -r requirements.txt
```
Create a `.env` file following what's listed in `.env.example`
```
WEB_BASE_URL = https://www.partselect.com
DEEPSEEK_API_KEY = <your API key>
...
```

### Running the program
Open two command line interfaces. Run ``npm run dev`` on one and ``python3 -m server.app`` (or whichever python version you have installed) on the other.

### Using Deepseek
I have implemented Deepseek into my program but have defaulted to using OpenAI. due to token access.

Helpful link: https://ai.pydantic.dev/models/openai/#openai-compatible-models

To use Deepseek, make sure to comment and comment out the following lines:
- `main.py` - lines 30-31 and lines 38-39
- `repair_tools.py` - lines 14-15, 24-25, 272-273, 386-387, and 428-429
- `repair_scraper.py` - lines 229-230, 248-249
- `supabase_tools.py` - lines 181-182
- `supabase_client.py` - lines 20-21