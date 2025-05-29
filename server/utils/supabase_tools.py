import csv
import os
import json
from pathlib import Path
from typing import Dict, Any, Optional
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel
import logging
from server.utils.supabase_client import SupabaseRAG

logger = logging.getLogger(__name__)

class SupabaseTools:
    """Tools for interacting with Supabase database"""
    
    def __init__(self, supabase_client: SupabaseRAG):
        self.supabase = supabase_client
    

    def execute_read_query(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a SQL query on Supabase"""
        query = args.get("query", "")
        
        if not query:
            return {"error": "No query provided"}
        
        # Only SELECT queries
        query_lower = query.lower().strip()
        if not query_lower.startswith("select"):
            return {"error": "Only SELECT queries are allowed"}
        
        try:
            # Execute query
            result = self.supabase.supabase.rpc(
                "execute_sql", 
                {"sql_query": query}
            ).execute()
            
            # Limit to 10
            data = result.data[:10] if result.data else []
            
            return {
                "success": True,
                "data": data,
                "count": len(data)
            }
        except Exception as e:
            logger.error(f"Error executing query: {str(e)}")
            return {"error": f"Query error: {str(e)}"}
    

    def search_rag(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Search using semantic search"""
        table = args.get("table", "")
        query = args.get("query", "")
        
        if not table or not query:
            return {"error": "Both table and query are required"}
        
        if table not in ["parts", "repairs", "blogs"]:
            return {"error": f"Invalid table: {table}. Valid options: parts, repairs, blogs"}
        
        try:
            results = self.supabase.semantic_search(table, query)
            return {
                "success": True,
                "results": results[:10],  # Limit to 10 results
                "count": len(results[:10])
            }
        except Exception as e:
            logger.error(f"Error in semantic search: {str(e)}")
            return {"error": f"Search error: {str(e)}"}
        

    async def search_blogs(self, query: str, appliance_type: Optional[str] = None, 
                brand: Optional[str] = None, threshold: float = 0.60, limit: int = 2) -> Dict[str, Any]:
        """
        Search blog articles using vector embeddings for semantic search
        """
        try:
            # Try LLM approach first for most intelligent matching
            llm_results = await self.search_blogs_llm(query, appliance_type, brand, limit)
            if llm_results["success"] and llm_results["articles"]:
                logger.info("Using LLM-matched blog articles")
                return llm_results
            
            csv_results = self.search_blogs_csv(query, appliance_type, brand, limit)
            if csv_results["success"] and csv_results["articles"]:
                return csv_results
            
            query_embedding = self.supabase.generate_embedding(query)
            
            if not query_embedding:
                logger.error("Failed to generate embedding for blog search query")
                return {"success": False, "message": "Failed to generate search embedding", "articles": []}
            
            # Construct Supabase query with embedding
            blogs_query = self.supabase.supabase.rpc(
                "match_blogs", 
                {
                    "query_embedding": query_embedding, 
                    "match_threshold": threshold,
                    "match_count": limit * 2  # Get more than needed for filtering
                }
            )
            
            if appliance_type:
                blogs_query = blogs_query.eq("appliance_type", appliance_type.lower())
            
            if brand:
                blogs_query = blogs_query.eq("brand", brand.lower())
            
            # Execute query
            result = blogs_query.execute()
            
            if not result.data:
                logger.info(f"No blog articles found for query: {query}")
                return {"success": True, "message": "No blog articles found", "articles": []}
            
            # Process results
            articles = []
            for item in result.data:
                articles.append({
                    "id": item["id"],
                    "title": item["title"],
                    "content": item["content"],
                    "url": item["url"],
                    "appliance_type": item["appliance_type"],
                    "brand": item["brand"],
                    "similarity": item["similarity"]
                })
            
            return {"success": True, "articles": articles}
        
        except Exception as e:
            logger.error(f"Error searching blog articles: {str(e)}")
            return self.search_blogs_csv(query, appliance_type, brand, limit)


    async def search_blogs_llm(self, query: str, appliance_type: Optional[str] = None,
                            brand: Optional[str] = None, limit: int = 2) -> Dict[str, Any]:
        """
        Use LLM to intelligently match user queries to blog articles with proper appliance type filtering
        """
        try:
            csv_path = os.path.join(Path(__file__).parent.parent.parent, 'blog_articles.csv')
            
            if not os.path.exists(csv_path):
                logger.error(f"Blog CSV file not found at {csv_path}")
                return {"success": False, "message": "Blog article database not available", "articles": []}
            
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                all_articles = list(reader)
            
            if not all_articles:
                return {"success": False, "message": "No articles found in database", "articles": []}
            
            filtered_articles = []
            for i, article in enumerate(all_articles):
                article_appliance = article.get("appliance_type", "").lower()
                
                if appliance_type and article_appliance:
                    norm_article_type = article_appliance.replace(" ", "")
                    norm_query_type = appliance_type.lower().replace(" ", "")
                    
                    if norm_query_type in ["fridge", "refrig"]:
                        norm_query_type = "refrigerator"
                    
                    if norm_article_type != norm_query_type:
                        continue
                
                filtered_articles.append({
                    "id": i,
                    "title": article.get("title", ""),
                    "appliance_type": article_appliance,
                    "url": article.get("url", "")
                })
            
            blog_agent = Agent(
                model=OpenAIModel('o3-mini', provider='openai'),
                # model=OpenAIModel('deepseek-chat', provider='deepseek'),
                system_prompt="You are an appliance repair expert that helps match user queries to the most relevant repair articles."
            )
            if appliance_type and not filtered_articles:
                logger.info(f"No articles found matching appliance type: {appliance_type}")
                return {"success": False, "message": f"No articles found for {appliance_type}", "articles": []}
        
            agent_prompt = f"""
            You are an appliance repair expert. Given the user's query about a {appliance_type or 'appliance'}, 
            identify the most relevant articles that would help answer the query.
            
            User's query: "{query}"
            Appliance type: {appliance_type or 'not specified'}
            
            Available blog articles (pre-filtered to match the correct appliance type):
            {json.dumps(filtered_articles, indent=2)}
            
            IMPORTANT: Only select articles that match the correct appliance type. 
            For example, DO NOT recommend dishwasher articles for refrigerator problems.
            
            Return a JSON array of the most relevant article IDs, ranked by relevance.
            Each entry should include:
            1. "id": the article ID number
            2. "relevance_score": a number between 0 and 1 indicating relevance
            3. "reason": a brief explanation of why this article is relevant
            
            Format your response as valid JSON only, with no additional text.
            """

            result = await blog_agent.run(agent_prompt)

            try:
                content = result.output
                relevant_articles = json.loads(content)
                
                if isinstance(relevant_articles, dict):
                    if "articles" in relevant_articles:
                        relevant_articles = relevant_articles["articles"]
                    elif "matches" in relevant_articles:
                        relevant_articles = relevant_articles["matches"]
                    elif not any(key in relevant_articles for key in ["id", "relevance_score"]):
                        relevant_articles = []
                
                articles = []
                for match in relevant_articles:
                    article_id = match.get("id")
                    if article_id is not None and 0 <= article_id < len(all_articles):
                        article = all_articles[article_id]
                        articles.append({
                            "title": article.get("title", ""),
                            "content": f"This article provides information about {article.get('title', '')}",
                            "url": article.get("url", ""),
                            "appliance_type": article.get("appliance_type", ""),
                            "similarity": match.get("relevance_score", 0.8),
                            "reason": match.get("reason", "Relevant to your question")
                        })
                
                # Sort by agent-assigned relevance
                articles.sort(key=lambda x: x["similarity"], reverse=True)
                
                logger.info(f"Agent found {len(articles)} relevant articles for query: {query}")
                if articles:
                    logger.info(f"Top match: {articles[0]['title']} - Reason: {articles[0].get('reason', 'No reason provided')}")
                    
                return {"success": True, "articles": articles[:limit]}
                
            except json.JSONDecodeError:
                logger.error(f"Failed to parse agent response: {content}")
                
        except Exception as e:
            logger.error(f"Error in agent blog search: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())


    def search_blogs_csv(self, query: str, appliance_type: Optional[str] = None, 
                        brand: Optional[str] = None, limit: int = 2) -> Dict[str, Any]:
        """
        Directly search blog articles from CSV file using flexible word matching
        that works for any appliance issue, not just ice makers
        """
        try:
            import csv
            import os
            import re
            from pathlib import Path
            
            csv_path = os.path.join(Path(__file__).parent.parent.parent, 'blog_articles.csv')
            
            if not os.path.exists(csv_path):
                logger.error(f"Blog CSV file not found at {csv_path}")
                return {"success": False, "message": "Blog article database not available", "articles": []}
            
            # Clean and tokenize query into meaningful words
            query_lower = query.lower()
            
            # Remove common stop words
            stop_words = {'a', 'an', 'the', 'and', 'or', 'but', 'is', 'are', 'was', 'were', 
                        'be', 'been', 'being', 'in', 'on', 'at', 'to', 'for', 'with', 'by', 
                        'about', 'how', 'can', 'i', 'my', 'me', 'mine', 'you', 'your', 'it'}
            
            # Split query into words and remove stop words
            query_words = set([w for w in re.findall(r'\b\w+\b', query_lower) 
                            if w not in stop_words and len(w) > 1])
            
            logger.info(f"Query words: {query_words}")
            
            articles = []
            
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    title = row.get('title', '').lower()
                    row_appliance = row.get('appliance_type', '').lower()
                    
                    # Calculate relevance score
                    score = 0
                    
                    # Exact brand match (highest priority)
                    if brand and brand.lower() in title:
                        score += 15
                        logger.info(f"Brand match in title: {row.get('title')}")
                    
                    # Appliance type match
                    if appliance_type and appliance_type.lower() in row_appliance:
                        score += 5
                    
                    # Tokenize the title
                    title_words = set(re.findall(r'\b\w+\b', title))
                    
                    # Calculate word overlap between query and title
                    matching_words = query_words.intersection(title_words)
                    
                    # Special keywords get extra weight
                    action_words = {'fix', 'repair', 'replace', 'clean', 'reset', 'install', 'remove'}
                    problem_words = {'not', 'broken', 'leaking', 'error', 'code', 'issues', 'problems', 'troubleshoot'}
                    
                    # Add points for each matching word
                    for word in matching_words:
                        points = 1  # Base point for any match
                        
                        # Action words (fix, repair, etc.) get bonus
                        if word in action_words:
                            points += 2
                            
                        # Problem words (broken, error, etc.) get bonus
                        elif word in problem_words:
                            points += 2
                            
                        # Longer words (likely more specific) get bonus
                        elif len(word) > 5:
                            points += 1
                        
                        score += points
                    
                    # Bonus for high percentage of matching words
                    if title_words and len(matching_words) / len(title_words) > 0.3:
                        score += 3
                        
                    # Only include if there's some relevance
                    if score > 0:
                        articles.append({
                            'title': row.get('title', ''),
                            'content': f"This article provides information about {row.get('title', '')}",
                            'url': row.get('url', ''),
                            'appliance_type': row_appliance,
                            'brand': brand if brand and brand.lower() in title else 'unknown',
                            'similarity': score / 20.0,  # Convert to 0-1 scale
                            'matching_words': list(matching_words)  # For debugging
                        })
                        
                        if score > 10:
                            logger.info(f"High score match ({score}): '{row.get('title')}' - Matching words: {matching_words}")
                
                # Sort by relevance score
                articles.sort(key=lambda x: x['similarity'], reverse=True)
                
                if articles:
                    logger.info(f"Found {len(articles)} relevant articles, top match: {articles[0]['title']}")
                else:
                    logger.info(f"No matching articles found for query: {query}")
                    
                return {"success": True, "articles": articles[:limit]}
        except Exception as e:
            logger.error(f"Error in CSV search: {str(e)}")
            return {"success": False, "message": f"Error searching articles: {str(e)}", "articles": []}
