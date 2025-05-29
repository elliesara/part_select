import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup
import logging
import time, os, re
from typing import Dict, Any, Optional
from dotenv import load_dotenv
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Web scraping constants
BASE_URL = os.getenv('WEB_BASE_URL')
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/113.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}
ACTUAL_URL = ""

class PartSelectScraper:
    """Webscraper class for PartSelect website"""

    @staticmethod
    def get_page_content(url: str, params: Dict[str, Any] = None) -> Optional[tuple[BeautifulSoup, str]]:
        """Use Selenium to get page content and return BeautifulSoup"""
        try:
            chrome_options = Options()
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--disable-extensions")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-notifications")
            chrome_options.add_argument("--disable-infobars")
            chrome_options.add_argument("--disable-web-security")
            
            chrome_options.add_argument("--headless")
            chrome_options.add_argument(f"user-agent={HEADERS['User-Agent']}")
            
            if not url.__contains__("Repair") and params:
                from urllib.parse import urlencode
                url = f"{url}?{urlencode(params)}"
            else:
                url = f"{url}"
                
            logger.info(f" Navigating to {url}")
            
            driver = webdriver.Chrome(options=chrome_options)
            driver.set_page_load_timeout(60)
            driver.get(url)
            
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            
            final_url = driver.current_url
            html = driver.page_source
            driver.quit()
            
            return BeautifulSoup(html, "html.parser"), final_url
        
        except Exception as e:
            logger.error(f"Failed to load webpage {url}: {str(e)}")
            return None


    @classmethod
    def search_part_by_number(cls, part_number: str) -> Dict[str, Any]:
        """Search for a part on PartSelect by part number"""
        
        # Try direct product URL first (more likely to succeed)
        product_url = f"{BASE_URL}/{part_number}-Whirlpool-WPW10321304-Refrigerator-Door-Shelf-Bin.htm"
        logger.info(f"Looking up part number {part_number}")
        result = cls.get_page_content(product_url)
        
        if not result:
            search_url = f"{BASE_URL}/PartSearch/Search.aspx"
            params = {"SearchTerm": f"{part_number}"}
            result = cls.get_page_content(search_url, params)
        
        if not result:
            return {"success": False, "message": f"Failed to fetch information for part number {part_number}"}
        
        soup, ACTUAL_URL = result
        
        try:
            name_elem = soup.select_one("h1.title-lg[itemprop='name']")
            name = name_elem.text.strip()
            
            price_elem = soup.select_one("span.price.pd__price span.js-partPrice")
            price = f"${price_elem.text.strip()}"
            
            description_elem = soup.select_one("div[itemprop='description']")
            description = description_elem.text.strip()
            
            image_elem = soup.select_one("div.main-media.MagicZoom-PartImage a.MagicZoom")
            image_url = image_elem['href'] if image_elem and 'href' in image_elem.attrs else ""

            if not image_url:
                image_elem = soup.select_one("div.main-media.MagicZoom-PartImage figure img")
                image_url = image_elem['src'] if image_elem and 'src' in image_elem.attrs else ""
            
            schematic_elem = soup.select_one("div.main-media.main-schematic a.MagicZoom")
            schematic_url = schematic_elem['href'] if schematic_elem and 'href' in schematic_elem.attrs else ""

            schematic_location = ""
            location_elem = soup.select_one("div.schematic-location")
            if location_elem:
                schematic_location = location_elem.text.strip()
            
            difficulty = "Not specified"
            difficulty_elem = soup.select_one("div.pd__repair-rating__container__item p.bold")
            if difficulty_elem:
                difficulty = difficulty_elem.text.strip()
            
            time_estimate = "Not specified"
            time_container = soup.select_one("div.d-flex:has(svg[href*='duration'])")
            if time_container:
                time_elem = time_container.select_one("p.bold")
                if time_elem:
                    time_estimate = time_elem.text.strip()
            
            rating_percent = "0%"
            review_count = "0"
            
            rating_elem = soup.select_one("div.rating__stars__upper")
            if rating_elem and 'style' in rating_elem.attrs:
                style = rating_elem['style']
                width_match = re.search(r'width:\s*(\d+)%', style)
                if width_match:
                    rating_percent = width_match.group(1) + "%"
            
            review_count_elem = soup.select_one("span.rating__count")
            if review_count_elem:
                review_count = review_count_elem.text.strip()

            return {
                "success": True,
                "part_info": {
                    "part_number": part_number,
                    "name": name,
                    "price": price,
                    "image_url": image_url,
                    "description": description,
                    "url": ACTUAL_URL,
                    "installation": {
                        "schematic_url": schematic_url,
                        "schematic_location": schematic_location,
                        "difficulty": difficulty,
                        "time_estimate": time_estimate
                    },
                    "ratings": {
                        "rating_percent": rating_percent,
                        "review_count": review_count
                    }
                }
            }
        except Exception as e:
            logger.error(f"Error parsing product page: {str(e)}")


    @classmethod
    def search_by_model(cls, model_number: str) -> Dict[str, Any]:
        """Search for compatible parts by model number"""
        model_url = f"{BASE_URL}/Models/{model_number}"
        
        result = cls.get_page_content(model_url)
        if not result:
            return {"success": False, "message": "Failed to fetch model information"}
        
        soup, final_url = result
        
        try:
            result = {"model": model_number, "url": final_url}
            
            # Find appliance info
            appliance_info = soup.find("div", class_="appliance-info")
            if appliance_info:
                result["appliance_type"] = appliance_info.find("h1").text.strip()
                result["brand"] = appliance_info.find("h2").text.strip()
            
            return {"success": True, "model_info": result}
        except Exception as e:
            logger.error(f"Error parsing model search results: {str(e)}")
            return {"success": False, "message": "Failed to parse model information"}


    @classmethod
    def check_compatibility(cls, part_number, model_number):
        """Check if a part is compatible with a specific model number, with fallbacks"""
        driver = None
        try:
            logger.info(f"Checking compatibility of part {part_number} with model {model_number}")
            
            if not part_number.upper().startswith("PS"):
                part_number = f"PS{part_number}"
                
            result = cls._try_compatibility_check_direct(part_number, model_number)
            print('====== RESULT 1', result)
            if result["success"] and "is_compatible" in result["compatibility_info"]:
                return result
                
            logger.info(f"Direct compatibility check failed, trying alternative method")
            
            print('====== RESULT 2', result)

            return result
        except Exception as e:
            logger.error(f"Error in compatibility check: {e}")
            if driver:
                try:
                    driver.quit()
                except:
                    pass
            return {
                "success": False, 
                "message": "I couldn't check compatibility at this time. Please try again later."
            }


    @classmethod
    def _try_compatibility_check_direct(cls, part_number, model_number):
        """Try direct compatibility check using part page compatibility tool"""
        driver = None
        try:
            # Try direct product URL first (more likely to succeed)
            product_url = f"{BASE_URL}/{part_number}-Whirlpool-WPW10321304-Refrigerator-Door-Shelf-Bin.htm"
            logger.info(f"Looking up part number {part_number} for compatibility check")
            result = cls.get_page_content(product_url)
            
            if not result:
                search_url = f"{BASE_URL}/PartSearch/Search.aspx"
                params = {"SearchTerm": f"{part_number}"}
                result = cls.get_page_content(search_url, params)
            
            if not result:
                return {"success": False, "message": f"Failed to fetch information for part number {part_number}"}
            
            # Get the actual part URL from the result
            _, part_url = result
            logger.info(f"Trying part page at: {part_url}")

            chrome_options = Options()
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--disable-extensions")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-notifications")
            chrome_options.add_argument("--disable-infobars")
            chrome_options.add_argument("--headless")
            chrome_options.add_argument(f"user-agent={HEADERS['User-Agent']}")
            
            driver = webdriver.Chrome(options=chrome_options)
            driver.set_page_load_timeout(30)
            
            # Navigate to part page
            logger.info(f"Navigating to 2 {part_url}")
            driver.get(part_url)
            time.sleep(2)
            
            # # Wait for compatibility search field
            # wait = WebDriverWait(driver, 15)
            # input_field = wait.until(EC.element_to_be_clickable(
            #     (By.CSS_SELECTOR, ".pd__compatibility-tool__search input")
            # ))
                    # Try to handle cookie banner/overlay first
            try:
                # Find and close cookie banners/overlays that might be blocking interaction
                overlays = driver.find_elements(By.CSS_SELECTOR, ".bx-slab, .cookie-banner, #cookie-consent, .modal-backdrop")
                for overlay in overlays:
                    logger.info("Attempting to remove overlay element")
                    driver.execute_script("arguments[0].remove();", overlay)
                    
                # Look for and click any close buttons or accept buttons
                close_buttons = driver.find_elements(By.CSS_SELECTOR, 
                    ".close-button, .close, .accept-cookies, button[aria-label='Close'], button[data-dismiss='modal']")
                for btn in close_buttons:
                    if btn.is_displayed():
                        logger.info("Clicking close button on overlay")
                        driver.execute_script("arguments[0].click();", btn)
            except Exception as e:
                logger.warning(f"Could not remove overlays: {e}, continuing anyway")
                
            # Wait for compatibility search field
            wait = WebDriverWait(driver, 5)
            try:
                input_field = wait.until(EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, ".pd__compatibility-tool__search input")
                ))
                
                # Use JavaScript to clear and fill the input field
                driver.execute_script("arguments[0].value = '';", input_field)
                driver.execute_script(f"arguments[0].value = '{model_number}';", input_field)
                logger.info(f"Entered model number: {model_number}")
                time.sleep(1)
                
                # Try to click the search button using JavaScript
                search_button = wait.until(EC.presence_of_element_located(
                    (By.CSS_SELECTOR, ".js-PCTSearchBtn")
                ))
                driver.execute_script("arguments[0].click();", search_button)
                logger.info("Clicked search button using JavaScript")
                
                # Wait for results to load
                time.sleep(3)
                
                # Take screenshot for debugging
                driver.save_screenshot("compatibility_result.png")
        
            except Exception as e:
                logger.warning(f"Could not interact with dropdown: {e}")
            
            html = driver.page_source
        
            soup = BeautifulSoup(html, "html.parser")
            
            # Check for compatibility indicators in the parsed HTML
            match_section = soup.select_one(".pd__compatibility-tool__match")
            no_match_section = soup.select_one(".pd__compatibility-tool__nomatch")

            side_ct = soup.select_one(".side-ct")
            
            is_compatible = False
            appliance_type = "appliance"
            message = ""
            model_details_url = ""
            if side_ct:
                is_compatible = True
                
                fit_message = side_ct.select_one("p.bold")
                if fit_message:
                    message = fit_message.text.strip()
                
                appliance_message = side_ct.select_one("h5.text-sm")
                if appliance_message:
                    appliance_text = appliance_message.text.strip()
                    if "fits your" in appliance_text:
                        appliance_type = appliance_text.split("fits your")[-1].strip()
            elif match_section:
                is_compatible = True
                
                title_elem = match_section.select_one(".title-md")
                if title_elem:
                    appliance_type = title_elem.text.strip()
                
                fit_message = match_section.get_text()
                if "It's a fit" in fit_message:
                    message = "It's a fit!"
                else:
                    message = "This part is compatible with your model."
                    
                # Get model details URL if available
                model_link = match_section.select_one("a.js-Link")
                if model_link and model_link.has_attr('href'):
                    model_details_url = model_link['href']
                    if not model_details_url.startswith('http'):
                        model_details_url = f"{BASE_URL}{model_details_url}"
            elif no_match_section:
                is_compatible = False
                
                message_elem = no_match_section.select_one("h5")
                if message_elem:
                    message = message_elem.text.strip()
                else:
                    message = "This part does not fit your model."
            else:
                message = "Could not determine compatibility from the website response."
            
            compatibility_info = {
                "is_compatible": is_compatible,
                "part_number": part_number,
                "model_number": model_number,
                "appliance_type": appliance_type,
                "message": message
            }

            print(compatibility_info)
            
            if model_details_url:
                compatibility_info["model_details_url"] = model_details_url
                
            driver.quit()
            return {"success": True, "compatibility_info": compatibility_info}
            
        except Exception as e:
            logger.error(f"Error in direct compatibility check: {e}")
            if driver:
                try:
                    driver.quit()
                except:
                    pass
            return {"success": False, "message": str(e)}


    @classmethod
    def troubleshoot(cls, product_type: str, issue: str) -> Dict[str, Any]:
        """Search for troubleshooting guides by appliance type and issue"""
        search_url = f"{BASE_URL}/Repair/{product_type}"
        
        soup = cls.get_page_content(search_url)
        if not soup:
            return {"success": False, "message": "Failed to fetch troubleshooting guides"}
        
        try:
            # Extract troubleshooting guides
            result = {"guides": []}
            
            guides_list = soup.find("div", class_="repair-guides")
            if not guides_list:
                return {"success": False, "message": f"No troubleshooting guides found for {product_type} {issue}"}
            
            for guide in guides_list.find_all("div", class_="guide-item"):
                guide_info = {
                    "title": guide.find("h3").text.strip(),
                    "summary": guide.find("p", class_="summary").text.strip(),
                    "url": BASE_URL + guide.find("a")["href"]
                }
                result["guides"].append(guide_info)
            
            return {"success": True, "troubleshooting_info": result}
        except Exception as e:
            logger.error(f"Error parsing troubleshooting search results: {str(e)}")
            return {"success": False, "message": "Failed to parse troubleshooting guides"}