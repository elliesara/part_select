import os
import sys
import json
import logging
import time
from typing import Dict, Any, List
from datetime import datetime

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.utils.repair_scraper import RepairKnowledgeScraper
from server.utils.supabase_client import SupabaseRAG
from dotenv import load_dotenv

load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def scrape_all_repair_guides():
    """Scrape all repair guides for all appliance types"""
    supabase = SupabaseRAG()
    
    # Get list of appliance types
    appliance_types = RepairKnowledgeScraper.get_appliance_types()
    
    total_guides = 0
    successful_guides = 0
    skipping_guides = 0
    
    for appliance in appliance_types:
        logger.info(f"Processing {appliance}...")
        
        # Get symptoms for this appliance
        symptoms = RepairKnowledgeScraper.get_symptoms_for_appliances(appliance)

        for symptom in symptoms:
            try:
                logger.info(f"Processing {appliance} - {symptom['name']}")
                
                json_filename = f"repair_guides/{appliance.lower()}_{symptom['id']}.json"

                if os.path.exists(json_filename):
                    with open(json_filename, "r") as f:
                        try:
                            repair_info = json.load(f)
                            logger.info(f"Found existing JSON file for {appliance} - {symptom['name']}")
                            
                            # Check if guide already exists in database
                            result = supabase.supabase.table("repairs").select("id").eq("appliance_type", appliance.lower()).eq("symptom", symptom["name"]).execute()
                            
                            if result.data and len(result.data) > 0:
                                logger.info(f"Repair guide already exists in database, skipping")
                                skipped_guides += 1
                                continue
                            else:
                                # Store existing JSON data in database
                                logger.info(f"JSON exists but not in database. Adding to database.")
                                store_result = RepairKnowledgeScraper.store_repair_guide(supabase, repair_info)
                                if store_result:
                                    successful_guides += 1
                                total_guides += 1
                                continue
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid JSON in {json_filename}, will re-scrape")

                # Check if guide already exists in database
                result = supabase.supabase.table("repairs").select("id").eq("appliance_type", appliance.lower()).eq("symptom", symptom["name"]).execute()
                
                if result.data and len(result.data) > 0:
                    logger.info(f"Repair guide for {appliance} - {symptom['name']} already exists, skipping")
                    skipped_guides += 1
                    continue
                
                # Extract repair guide
                guide_result = RepairKnowledgeScraper.extract_repair_guide(appliance, symptom)

                if guide_result["success"]:
                    # Store in database
                    store_result = RepairKnowledgeScraper.store_repair_guide(supabase, guide_result["repair_info"])
                    print('\n ------------- \n', 'SUCCESS', '\n ------------- \n')
                    if store_result:
                        successful_guides += 1
                    
                    # Save a backup to JSON file
                    with open(json_filename, "w") as f:
                        json.dump(guide_result["repair_info"], f, indent=2)
                
                total_guides += 1
                
                # Be nice to the server with a pause
                time.sleep(2)
                
            except Exception as e:
                logger.error(f"Error processing {appliance} - {symptom['name']}: {e}")
    
    logger.info(f"Scraping complete. Processed {total_guides} guides, successfully stored {successful_guides}")


if __name__ == "__main__":
    # Create backup directory if it doesn't exist
    os.makedirs("repair_guides", exist_ok=True)
    
    # Run the scraper
    scrape_all_repair_guides()