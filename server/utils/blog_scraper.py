import os, sys, csv
import logging
import time
from typing import Dict, Any, List
from bs4 import BeautifulSoup
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from dotenv import load_dotenv

from server.utils.supabase_client import SupabaseRAG

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

supabase_rag = SupabaseRAG()


BASE_URL = "https://www.partselect.com"
BLOG_BASE_URL = f"{BASE_URL}/content/blog"
CSV_OUTPUT_FILE = "appliance_blogs.csv"

APPLIANCE_KEYWORDS = {
    'refrigerator': [
        'fridge', 'refrigerator', 'freezer', 'ice maker', 'ice-maker', 'cooling', 'ice', 
        'refrigeration', 'cooler', 'cold', 'temperature', 'food storage'
    ],
    'dishwasher': [
        'dishwasher', 'dish washer', 'dishes', 'rinse', 'wash cycle',
        'clean dishes', 'detergent', 'dishwashing', 'dish soap',
    ]
}


def extract_blog_links(soup):
    """Extract blog links with fixed selectors based on actual HTML structure"""
    blog_links = []
    
    # Get both types of articles
    articles = soup.select("a.article-card, a.blog__hero-article")
    logger.info(f"Found {len(articles)} article elements")
    
    for article in articles:
        href = article.get('href', '')
        
        # Different class structure for hero vs regular articles
        if 'blog__hero-article' in article.get('class', []):
            title_tag = article.select_one('h1.title-lg')
        else:
            # Using div.article-card__title based on your HTML example
            title_tag = article.select_one('div.article-card__title')
        
        title = title_tag.text.strip() if title_tag else ''
        
        desc_tag = article.select_one('p')
        description = desc_tag.text.strip() if desc_tag else ''
        
        if title:
            blog_links.append({
                'url': href,
                'title': title,
                'description': description,
                'full_url': BASE_URL + href if href.startswith('/') else href
            })
            logger.info(f"Found article: {title}")
        
    return blog_links


def is_appliance_related(title, description):
    """Check if an article is related to fridges or dishwashers"""
    combined_text = (title + ' ' + description).lower()
    
    for appliance_type, keywords in APPLIANCE_KEYWORDS.items():
        for keyword in keywords:
            if keyword in combined_text:
                logger.info(f"✅ '{keyword}' found in: '{title}' => {appliance_type}")
                return appliance_type
    return ""


def fetch_blog_content(url):
    """Fetch and parse a blog article's content"""
    logger.info(f"Fetching article: {url}")
    
    try:
        response = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        title = soup.select_one('h1.blog-article__title')
        title = title.text.strip() if title else ''
        
        content_div = soup.select_one('div.blog-article__content')
        content_text = ''
        html_content = ''
        
        if content_div:
            content_text = ' '.join([p.text.strip() for p in content_div.find_all('p')])
            html_content = str(content_div)
        
        date = soup.select_one('div.blog-article__date')
        date = date.text.strip() if date else ''
        
        author = soup.select_one('div.blog-article__author')
        author = author.text.strip() if author else ''
        
        # Debug log the title to confirm we're getting it
        logger.info(f"Found article title: {title}")
        
        # Return a complete article dictionary
        return {
            'title': title,
            'content': content_text,
            'html_content': html_content,
            'date': date,
            'author': author,
            'url': url,  # Ensure URL is set correctly
            'related_parts': []
        }
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return None


def save_to_supabase(article, appliance_type):
    """Save article to Supabase using SupabaseRAG for embedding generation"""
    try:
        brands = ['whirlpool', 'ge', 'samsung', 'lg', 'bosch', 'kitchenaid', 'frigidaire', 
                'maytag', 'kenmore', 'electrolux', 'amana']
        combined_text = (article['title'] + ' ' + article['content']).lower()
        detected_brands = [brand for brand in brands if brand in combined_text]
        brand = detected_brands[0] if detected_brands else 'generic'
        
        logger.info(f"Saving article: '{article['title']}' (Type: {appliance_type}, Brand: {brand})")
        
        # Generate embedding using supabase_rag
        embedding = supabase_rag.generate_embedding(article['content'])
        if not embedding:
            logger.error("Failed to generate embedding")
            return False
            
        logger.info(f"Generated embedding of length: {len(embedding)}")
        
        # Save to database using supabase_rag.supabase
        result = supabase_rag.supabase.table('blogs').insert({
            'title': article['title'],
            'content': article['content'],
            'html_content': article['html_content'],
            'url': article['url'],
            'date_published': article['date'],
            'author': article['author'],
            'appliance_type': appliance_type,
            'brand': brand,
            'related_parts': '[]',
            'embedding': embedding,
            'created_at': 'now()',
            'updated_at': 'now()'
        }).execute()
        
        logger.info(f"✅ Successfully saved to Supabase: {article['title']}")
        return True
    except Exception as e:
        logger.error(f"Error saving to Supabase: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False
    

def scrape_blogs():
    """Main function to scrape blogs using Selenium for better JS support"""
    all_blog_links = []
    
    chrome_options = Options()
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument(f"user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")
    
    driver = webdriver.Chrome(options=chrome_options)
    driver.set_page_load_timeout(30)
    
    try:
        for page_num in range(1, 10):  # Up to 10 pages
            page_url = f"{BLOG_BASE_URL}?start={page_num}"
            
            logger.info(f"Fetching blog index page {page_num}: {page_url}")
            
            driver.get(page_url)
            time.sleep(3)
            
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            blog_links = extract_blog_links(soup)
            
            if not blog_links:
                logger.info(f"No more articles found on page {page_num}")
                break
                
            all_blog_links.extend(blog_links)
            logger.info(f"Total articles found so far: {len(all_blog_links)}")
        
        filtered_links = []
        for link in all_blog_links:
            appliance_type = is_appliance_related(link['title'], link['description'])
            if appliance_type:
                link['appliance_type'] = appliance_type
                filtered_links.append(link)
        
        logger.info(f"Found {len(filtered_links)} appliance-related articles out of {len(all_blog_links)}")
        
        articles_data = []

        for link in filtered_links:
            articles_data.append({
                'appliance_type': link['appliance_type'],
                'title': link['title'],
                'url': link['full_url']
            })

        logger.info(f"Preparing to save {len(articles_data)} articles to CSV file")

        export_articles_to_csv(articles_data)
            
    finally:
        driver.quit()
        csv_path = os.path.abspath("blog_articles.csv")
        logger.info(f"CSV saved at: {csv_path}")


def export_articles_to_csv(articles, filename="blog_articles.csv"):
    """Export a simplified list of articles to CSV with just title and URL"""
    try:
        if not articles:
            logger.warning("No articles to export to CSV!")
            return False
            
        with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['title', 'url', 'appliance_type']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            writer.writeheader()
            for article in articles:
                logger.debug(f"Writing article to CSV: {article.get('title', 'Unknown')}")
                writer.writerow({
                    'title': article.get('title', 'Unknown Title'),
                    'url': article.get('url', 'Unknown URL'),
                    'appliance_type': article.get('appliance_type', '')
                })
                
        logger.info(f"✅ Successfully saved {len(articles)} articles to {filename}")
        return True
    except Exception as e:
        logger.error(f"Error saving to CSV: {e}")
        return False


if __name__ == "__main__":
    scrape_blogs()