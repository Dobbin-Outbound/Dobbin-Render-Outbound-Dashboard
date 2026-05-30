"""
HeyReach API Client
Handles all interactions with HeyReach API for LinkedIn outreach data
"""

import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging
import json
import gc
import time

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
# Reduce verbosity for requests library to minimize memory from log strings
logging.getLogger('requests').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)


class HeyReachClient:
    """Client for interacting with HeyReach API"""
    
    def __init__(self, api_key: str, base_url: str = "https://api.heyreach.io", 
                 sender_ids: List[int] = None, sender_names: Dict[int, str] = None,
                 client_groups: Dict = None):
        """
        Initialize HeyReach client
        
        Args:
            api_key: HeyReach API key
            base_url: Base URL for HeyReach API
            sender_ids: Optional list of manually configured sender IDs
            sender_names: Optional dict mapping sender IDs to names
            client_groups: Optional dict mapping client names to sender IDs
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.headers = {
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        # Create a session for connection pooling and reuse
        # This significantly improves performance for multiple API calls
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        
        # Configure connection pooling
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        
        # Retry strategy for transient errors
        # For 429 (rate limit), use longer backoff; for other errors, use shorter backoff
        retry_strategy = Retry(
            total=5,  # Increased retries
            backoff_factor=2.0,  # Increased backoff: 2s, 4s, 8s, 16s, 32s
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            respect_retry_after_header=True  # Respect Retry-After header from API
        )
        
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=5,   # Reduced for Render free plan memory limits
            pool_maxsize=5,       # Match max_workers to minimize memory usage
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # Store working endpoints (discovered dynamically)
        self.working_endpoints = {}
        self.manual_sender_ids = sender_ids or []  # Manually configured sender IDs
        self.manual_sender_names = sender_names or {}  # Manually configured sender names
        self.client_groups = client_groups or {}  # Client groups for organizing senders
        
        # Create reverse mapping: sender_id -> client_name
        self.sender_to_client = {}
        if self.client_groups:
            for client_name, client_data in self.client_groups.items():
                if isinstance(client_data, dict):
                    sender_list = client_data.get('sender_ids', [])
                elif isinstance(client_data, list):
                    sender_list = client_data
                else:
                    continue
                for sender_id in sender_list:
                    try:
                        sender_id_int = int(sender_id) if isinstance(sender_id, str) else sender_id
                        self.sender_to_client[sender_id_int] = client_name
                    except (ValueError, TypeError):
                        pass
    
    def _make_request(self, endpoint: str, method: str = "GET", params: Dict = None, data: Dict = None, headers: Dict = None) -> Dict:
        """
        Make API request to HeyReach
        
        Args:
            endpoint: API endpoint
            method: HTTP method
            params: Query parameters
            data: Request body data
            headers: Optional custom headers
            
        Returns:
            Response data as dictionary
        """
        url = f"{self.base_url}/{endpoint}"
        request_headers = headers or self.headers
        
        try:
            # Use session for connection pooling and reuse
            response = self.session.request(
                method=method,
                url=url,
                headers=request_headers,
                params=params,
                json=data,
                timeout=(10, 60)  # (connect timeout, read timeout) - increased read timeout for large responses
            )
            
            # Log detailed error information for debugging
            if response.status_code != 200:
                logger.error(f"API Error - Status: {response.status_code}, URL: {url}")
                try:
                    error_text = response.text[:500]
                    logger.error(f"Response: {error_text}")
                except:
                    pass
            
            response.raise_for_status()
            
            # Handle different response content types
            content_type = response.headers.get('Content-Type', '').lower()
            
            # Try to parse as JSON first
            try:
                if 'application/json' in content_type or 'text/json' in content_type:
                    return response.json()
                elif 'text/plain' in content_type or 'text/html' in content_type:
                    # Try to parse as JSON even if content-type says text/plain
                    try:
                        return response.json()
                    except:
                        # If JSON parsing fails, try to extract JSON from text
                        text = response.text.strip()
                        # Try to find JSON in the response
                        if text.startswith('{') or text.startswith('['):
                            import json
                            return json.loads(text)
                        else:
                            logger.warning(f"Response is text but not JSON: {text[:200]}")
                            return {}
                else:
                    # Try JSON anyway
                    return response.json()
            except ValueError as json_error:
                logger.error(f"Failed to parse JSON response: {json_error}")
                logger.error(f"Response content type: {content_type}")
                logger.error(f"Response text (first 500 chars): {response.text[:500]}")
                return {}
                
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP Error: {e}")
            logger.error(f"URL: {url}")
            logger.error(f"Status Code: {e.response.status_code if e.response else 'Unknown'}")
            try:
                error_text = e.response.text[:1000] if e.response else 'No response'
                logger.error(f"Response: {error_text}")
                # Try to parse error response as JSON
                if e.response:
                    try:
                        error_json = e.response.json()
                        logger.error(f"Error JSON: {json.dumps(error_json, indent=2)}")
                    except:
                        pass
            except:
                pass
            return {}
        except requests.exceptions.RequestException as e:
            logger.error(f"HeyReach API error: {e}")
            return {}
    
    def get_campaigns(self) -> List[Dict]:
        """
        Get all LinkedIn campaigns
        
        Returns:
            List of campaign dictionaries
        """
        logger.info("Fetching HeyReach campaigns...")
        
        # Check if we already know a working endpoint
        if 'campaigns' in self.working_endpoints:
            endpoint = self.working_endpoints['campaigns']
            logger.info(f"Using cached working endpoint: {endpoint}")
            data = self._make_request(endpoint, method="POST", data={
                "offset": 0,
                "keyword": "",
                "statuses": [],
                "accountIds": [],
                "limit": 100
            })
            if data and isinstance(data, dict):
                if 'items' in data:
                    return data.get('items', [])
                elif 'data' in data:
                    return data.get('data', [])
            elif isinstance(data, list):
                return data
        
        # Try different endpoint variations
        endpoints_to_try = [
            "api/public/campaign/GetAll",
            "api/public/campaign/getAll",
            "api/public/campaigns",
            "api/v1/campaign",
            "api/v1/campaigns",
            "api/campaign",
            "api/campaigns",
            "campaign/GetAll",
        ]
        
        request_data = {
            "offset": 0,
            "keyword": "",
            "statuses": [],
            "accountIds": [],
            "limit": 100
        }
        
        for endpoint in endpoints_to_try:
            try:
                logger.debug(f"Trying endpoint: {endpoint}")
                data = self._make_request(endpoint, method="POST", data=request_data)
                
                if data and isinstance(data, dict):
                    if 'items' in data:
                        logger.info(f"✅ Successfully fetched campaigns from: {endpoint}")
                        self.working_endpoints['campaigns'] = endpoint
                        return data.get('items', [])
                    elif 'data' in data:
                        logger.info(f"✅ Successfully fetched campaigns from: {endpoint}")
                        self.working_endpoints['campaigns'] = endpoint
                        return data.get('data', [])
                elif isinstance(data, list):
                    logger.info(f"✅ Successfully fetched campaigns from: {endpoint}")
                    self.working_endpoints['campaigns'] = endpoint
                    return data
            except Exception as e:
                logger.debug(f"Endpoint {endpoint} failed: {str(e)[:100]}")
                continue
        
        logger.warning("⚠️ All endpoint variations failed for campaigns")
        return []
    
    def get_campaign_stats(self, campaign_id: str, start_date: str = None, end_date: str = None) -> Dict:
        """
        Get statistics for a specific campaign
        
        Args:
            campaign_id: Campaign ID
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            
        Returns:
            Campaign statistics dictionary
        """
        params = {
            "campaign_id": campaign_id
        }
        
        if start_date:
            params['start_date'] = start_date
        if end_date:
            params['end_date'] = end_date
            
        logger.info(f"Fetching stats for campaign {campaign_id}...")
        return self._make_request(f"campaigns/{campaign_id}/stats", params=params)
    
    def get_all_campaign_stats(self, days_back: int = 7) -> List[Dict]:
        """
        Get statistics for all campaigns
        
        Args:
            days_back: Number of days to look back
            
        Returns:
            List of campaign statistics
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)
        
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        
        campaigns = self.get_campaigns()
        all_stats = []
        
        for campaign in campaigns:
            campaign_id = campaign.get('id')
            if campaign_id:
                stats = self.get_campaign_stats(
                    campaign_id=campaign_id,
                    start_date=start_str,
                    end_date=end_str
                )
                
                # Combine campaign info with stats
                combined = {
                    'campaign_id': campaign_id,
                    'campaign_name': campaign.get('name', 'Unknown'),
                    'status': campaign.get('status', 'unknown'),
                    **stats
                }
                all_stats.append(combined)
        
        return all_stats
    
    def get_connections_data(self, days_back: int = 7) -> Dict:
        """
        Get LinkedIn connection request data
        
        Args:
            days_back: Number of days to look back
            
        Returns:
            Connection data summary
        """
        params = {
            'days_back': days_back
        }
        
        logger.info("Fetching connection data...")
        return self._make_request("connections", params=params)
    
    def get_messages_data(self, days_back: int = 7) -> Dict:
        """
        Get LinkedIn messages data
        
        Args:
            days_back: Number of days to look back
            
        Returns:
            Messages data summary
        """
        params = {
            'days_back': days_back
        }
        
        logger.info("Fetching messages data...")
        return self._make_request("messages", params=params)
    
    def get_linkedin_accounts(self, force_api: bool = False) -> List[Dict]:
        """
        Get all LinkedIn accounts (senders)
        
        Args:
            force_api: If True, always fetch from API even if manual sender IDs are configured
        
        Returns:
            List of LinkedIn account dictionaries
        """
        # If force_api is False, prioritize manually configured sender IDs if available
        # This allows using config.yaml sender IDs when appropriate
        # But when force_api=True (e.g., when user enters API key), always fetch from API
        if not force_api and self.manual_sender_ids is not None and len(self.manual_sender_ids) > 0:
            logger.info(f"Using {len(self.manual_sender_ids)} manually configured sender IDs instead of API")
            accounts = []
            for sender_id in self.manual_sender_ids:
                sender_name = self.manual_sender_names.get(sender_id, f'Sender {sender_id}')
                accounts.append({
                    'id': sender_id,
                    'linkedInUserListName': sender_name,
                    'name': sender_name
                })
            logger.info(f"[OK] Returning {len(accounts)} senders from manual configuration")
            return accounts
        
        logger.info("Fetching LinkedIn accounts from API...")
        
        # Check if we already know a working endpoint
        if 'linkedin_accounts' in self.working_endpoints:
            endpoint = self.working_endpoints['linkedin_accounts']
            logger.debug(f"Using cached working endpoint: {endpoint}")
            try:
                data = self._make_request(endpoint, method="POST", data={
                    "offset": 0,
                    "limit": 100
                })
                items = None
                if data and isinstance(data, dict):
                    if 'items' in data:
                        items = data.get('items', [])
                    elif 'data' in data:
                        items = data.get('data', [])
                elif isinstance(data, list) and len(data) > 0:
                    items = data
                
                if items:
                    logger.info(f"✅ Successfully fetched {len(items)} accounts from cached endpoint")
                    # Map account IDs to names from config.yaml
                    mapped_accounts = []
                    for account in items:
                        account_id = account.get('id')
                        if account_id:
                            # Convert account_id to int for lookup
                            account_id_int = int(account_id) if account_id and isinstance(account_id, (str, float)) else account_id
                            # Get name from config.yaml if available
                            mapped_name = (
                                self.manual_sender_names.get(account_id_int) or 
                                self.manual_sender_names.get(account_id) or
                                account.get('linkedInUserListName') or 
                                account.get('name') or 
                                f'Sender {account_id}'
                            )
                            # Update account with mapped name
                            account['linkedInUserListName'] = mapped_name
                            account['name'] = mapped_name
                        mapped_accounts.append(account)
                    return mapped_accounts
            except Exception as e:
                logger.debug(f"Cached endpoint failed: {e}")
                # Remove from cache and try other endpoints
                del self.working_endpoints['linkedin_accounts']
        
        # Try a limited set of endpoint variations (reduce logging spam)
        # User specified the correct endpoint: api/public/li_account/GetAll
        endpoints_to_try = [
            "api/public/li_account/GetAll",  # Correct endpoint as specified by user
            "api/public/linkedin-account/GetAll",
            "api/public/linkedinAccount/GetAll",
            "api/v1/accounts",
            "api/public/accounts",
        ]
        
        # Try different header variations
        header_variations = [
            {
                "X-API-KEY": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            {
                "X-API-KEY": self.api_key,
                "Content-Type": "application/json",
                "Accept": "text/plain"
            },
        ]
        
        request_data = {
            "offset": 0,
            "limit": 100
        }
        
        # Try only a few combinations to avoid spam
        for headers in header_variations[:1]:  # Only try first header variation
            for endpoint in endpoints_to_try[:2]:  # Only try first 2 endpoints
                try:
                    logger.debug(f"Trying endpoint: {endpoint}")
                    data = self._make_request(endpoint, method="POST", data=request_data, headers=headers)
                    
                    if data and isinstance(data, dict):
                        # Check for different response structures
                        if 'items' in data:
                            items = data.get('items', [])
                            if items:
                                logger.info(f"✅ Successfully fetched {len(items)} accounts from: {endpoint}")
                                self.working_endpoints['linkedin_accounts'] = endpoint
                                self.headers = headers
                                # Map account IDs to names from config.yaml
                                mapped_accounts = []
                                for account in items:
                                    account_id = account.get('id')
                                    if account_id:
                                        # Convert account_id to int for lookup
                                        account_id_int = int(account_id) if account_id and isinstance(account_id, (str, float)) else account_id
                                        # Get name from config.yaml if available
                                        mapped_name = (
                                            self.manual_sender_names.get(account_id_int) or 
                                            self.manual_sender_names.get(account_id) or
                                            account.get('linkedInUserListName') or 
                                            account.get('name') or 
                                            f'Sender {account_id}'
                                        )
                                        # Update account with mapped name
                                        account['linkedInUserListName'] = mapped_name
                                        account['name'] = mapped_name
                                    mapped_accounts.append(account)
                                return mapped_accounts
                        elif 'data' in data:
                            items = data.get('data', [])
                            if items:
                                logger.info(f"✅ Successfully fetched {len(items)} accounts from: {endpoint}")
                                self.working_endpoints['linkedin_accounts'] = endpoint
                                self.headers = headers
                                # Map account IDs to names from config.yaml
                                mapped_accounts = []
                                for account in items:
                                    account_id = account.get('id')
                                    if account_id:
                                        # Convert account_id to int for lookup
                                        account_id_int = int(account_id) if account_id and isinstance(account_id, (str, float)) else account_id
                                        # Get name from config.yaml if available
                                        mapped_name = (
                                            self.manual_sender_names.get(account_id_int) or 
                                            self.manual_sender_names.get(account_id) or
                                            account.get('linkedInUserListName') or 
                                            account.get('name') or 
                                            f'Sender {account_id}'
                                        )
                                        # Update account with mapped name
                                        account['linkedInUserListName'] = mapped_name
                                        account['name'] = mapped_name
                                    mapped_accounts.append(account)
                                return mapped_accounts
                    elif isinstance(data, list) and len(data) > 0:
                        logger.info(f"✅ Successfully fetched {len(data)} accounts from: {endpoint}")
                        self.working_endpoints['linkedin_accounts'] = endpoint
                        self.headers = headers
                        # Map account IDs to names from config.yaml
                        mapped_accounts = []
                        for account in data:
                            account_id = account.get('id')
                            if account_id:
                                # Convert account_id to int for lookup
                                account_id_int = int(account_id) if account_id and isinstance(account_id, (str, float)) else account_id
                                # Get name from config.yaml if available
                                mapped_name = (
                                    self.manual_sender_names.get(account_id_int) or 
                                    self.manual_sender_names.get(account_id) or
                                    account.get('linkedInUserListName') or 
                                    account.get('name') or 
                                    f'Sender {account_id}'
                                )
                                # Update account with mapped name
                                account['linkedInUserListName'] = mapped_name
                                account['name'] = mapped_name
                            mapped_accounts.append(account)
                        return mapped_accounts
                except Exception as e:
                    # Only log errors, not debug messages for each failure
                    if "404" not in str(e):
                        logger.debug(f"Endpoint {endpoint} failed: {str(e)[:100]}")
                    continue
        
        # If API failed and we don't have manual sender IDs, log warning
        if not self.manual_sender_ids or len(self.manual_sender_ids) == 0:
            logger.warning("⚠️ Could not fetch LinkedIn accounts from API and no manual sender IDs configured")
            logger.info("💡 Solution: Add sender_ids to config.yaml under heyreach section")
        else:
            # Fallback: if API failed but we have manual sender IDs, use them
            logger.info(f"API call failed, falling back to {len(self.manual_sender_ids)} manually configured sender IDs")
            accounts = []
            for sender_id in self.manual_sender_ids:
                sender_name = self.manual_sender_names.get(sender_id, f'Sender {sender_id}')
                accounts.append({
                    'id': sender_id,
                    'linkedInUserListName': sender_name,
                    'name': sender_name
                })
            return accounts
        
        return []
    
    def get_summary_metrics(self, days_back: int = 7) -> Dict:
        """
        Get summary metrics grouped by sender (LinkedIn account)
        
        Args:
            days_back: Number of days to look back
            
        Returns:
            Summary metrics dictionary
        """
        campaigns = self.get_campaigns()
        linkedin_accounts = self.get_linkedin_accounts()
        
        # Group campaigns by LinkedIn account
        sender_data = []
        
        for account in linkedin_accounts:
            account_id = account.get('id')
            account_name = account.get('linkedInUserListName', 'Unknown')
            
            # Find campaigns for this account
            account_campaigns = [c for c in campaigns if c.get('linkedInUserListId') == account_id]
            
            # Aggregate metrics for this sender
            total_invites_sent = 0
            total_invites_accepted = 0
            total_messages_sent = 0
            total_replies = 0
            
            for campaign in account_campaigns:
                # These field names are assumptions - adjust based on actual API response
                total_invites_sent += campaign.get('connectionRequestsSent', 0)
                total_invites_accepted += campaign.get('connectionsAccepted', 0)
                total_messages_sent += campaign.get('messagesSent', 0)
                total_replies += campaign.get('repliesReceived', 0)
            
            acceptance_rate = (total_invites_accepted / total_invites_sent * 100) if total_invites_sent > 0 else 0
            reply_rate = (total_replies / total_messages_sent * 100) if total_messages_sent > 0 else 0
            
            sender_data.append({
                'sender_id': account_id,
                'sender_name': account_name,
                'invites_sent': total_invites_sent,
                'invites_accepted': total_invites_accepted,
                'acceptance_rate': round(acceptance_rate, 2),
                'messages_sent': total_messages_sent,
                'replies': total_replies,
                'reply_rate': round(reply_rate, 2),
                'campaigns_count': len(account_campaigns)
            })
        
        # Calculate overall totals
        total_invites_sent = sum(s['invites_sent'] for s in sender_data)
        total_invites_accepted = sum(s['invites_accepted'] for s in sender_data)
        total_messages_sent = sum(s['messages_sent'] for s in sender_data)
        total_replies = sum(s['replies'] for s in sender_data)
        
        overall_acceptance_rate = (total_invites_accepted / total_invites_sent * 100) if total_invites_sent > 0 else 0
        overall_reply_rate = (total_replies / total_messages_sent * 100) if total_messages_sent > 0 else 0
        
        return {
            'platform': 'LinkedIn (HeyReach)',
            'date_range_days': days_back,
            'total_senders': len(sender_data),
            'total_campaigns': len(campaigns),
            'total_invites_sent': total_invites_sent,
            'total_invites_accepted': total_invites_accepted,
            'acceptance_rate': round(overall_acceptance_rate, 2),
            'total_messages_sent': total_messages_sent,
            'total_replies': total_replies,
            'reply_rate': round(overall_reply_rate, 2),
            'senders_data': sender_data,
            'campaigns_data': campaigns
        }
    
    def get_campaign_details(self, campaign_id: str) -> Dict:
        """
        Get detailed campaign information including statistics
        
        Args:
            campaign_id: Campaign ID
            
        Returns:
            Campaign details dictionary
        """
        logger.info(f"Fetching campaign details for {campaign_id}...")
        data = self._make_request(f"api/public/campaign/Get", method="POST", data={
            "id": campaign_id
        })
        return data
    
    def get_leads(self, campaign_id: str = None, start_date: str = None, end_date: str = None, 
                  linkedin_account_id: str = None, limit: int = 1000) -> List[Dict]:
        """
        Get leads data from campaigns
        
        Args:
            campaign_id: Optional campaign ID filter
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            linkedin_account_id: Optional LinkedIn account ID filter
            limit: Maximum number of leads to return
            
        Returns:
            List of lead dictionaries
        """
        logger.info("Fetching leads data...")
        
        # Check if we already know a working endpoint
        if 'leads' in self.working_endpoints:
            endpoint = self.working_endpoints['leads']
            logger.info(f"Using cached working endpoint: {endpoint}")
            request_data = {
                "offset": 0,
                "limit": limit,
                "keyword": "",
                "campaignIds": [campaign_id] if campaign_id else [],
                "linkedInAccountIds": [linkedin_account_id] if linkedin_account_id else [],
                "statuses": [],
                "tags": []
            }
            if start_date and end_date:
                request_data["startDate"] = start_date
                request_data["endDate"] = end_date
            
            data = self._make_request(endpoint, method="POST", data=request_data)
            if data and isinstance(data, dict):
                if 'items' in data:
                    return data.get('items', [])
                elif 'data' in data:
                    return data.get('data', [])
            elif isinstance(data, list):
                return data
        
        request_data = {
            "offset": 0,
            "limit": limit,
            "keyword": "",
            "campaignIds": [campaign_id] if campaign_id else [],
            "linkedInAccountIds": [linkedin_account_id] if linkedin_account_id else [],
            "statuses": [],
            "tags": []
        }
        
        if start_date and end_date:
            request_data["startDate"] = start_date
            request_data["endDate"] = end_date
        
        # Try different endpoint variations
        endpoints_to_try = [
            "api/public/lead/GetAll",
            "api/public/lead/getAll",
            "api/public/leads",
            "api/v1/lead",
            "api/v1/leads",
            "api/lead",
            "api/leads",
            "lead/GetAll",
        ]
        
        for endpoint in endpoints_to_try:
            try:
                logger.debug(f"Trying endpoint: {endpoint}")
                data = self._make_request(endpoint, method="POST", data=request_data)
                
                if data and isinstance(data, dict):
                    if 'items' in data:
                        logger.info(f"✅ Successfully fetched leads from: {endpoint}")
                        self.working_endpoints['leads'] = endpoint
                        return data.get('items', [])
                    elif 'data' in data:
                        logger.info(f"✅ Successfully fetched leads from: {endpoint}")
                        self.working_endpoints['leads'] = endpoint
                        return data.get('data', [])
                elif isinstance(data, list):
                    logger.info(f"✅ Successfully fetched leads from: {endpoint}")
                    self.working_endpoints['leads'] = endpoint
                    return data
            except Exception as e:
                logger.debug(f"Endpoint {endpoint} failed: {str(e)[:100]}")
                continue
        
        logger.warning("⚠️ All endpoint variations failed for leads")
        return []
    
    def _generate_weeks(self, start_date_obj: datetime, end_date_obj: datetime) -> List[Dict]:
        """
        Generate list of weeks (Saturday to Friday) for the given date range
        
        Args:
            start_date_obj: Start date as datetime object
            end_date_obj: End date as datetime object
            
        Returns:
            List of week dictionaries with 'start', 'end', and 'key' fields
        """
        weeks = []
        
        # Find the Saturday that starts the week containing start_date
        start_weekday = start_date_obj.weekday()
        
        # Calculate days to go back to get to Saturday
        # If it's Saturday (5), we're already there (0 days back)
        # If it's Sunday (6), go back 1 day
        # If it's Monday (0), go back 2 days
        # If it's Tuesday (1), go back 3 days
        # If it's Wednesday (2), go back 4 days
        # If it's Thursday (3), go back 5 days
        # If it's Friday (4), go back 6 days
        if start_weekday == 5:  # Saturday
            days_back = 0
        elif start_weekday == 6:  # Sunday
            days_back = 1
        else:  # Monday (0) through Friday (4)
            days_back = start_weekday + 2
        
        # Find the Saturday that starts the week
        first_saturday = start_date_obj - timedelta(days=days_back)
        
        # Generate week ranges
        current_week_start = first_saturday
        week_count = 0
        max_weeks = 52  # Safety limit to prevent infinite loops
        
        while current_week_start <= end_date_obj and week_count < max_weeks:
            week_end = current_week_start + timedelta(days=6)  # Friday of this week
            
            # Only add week if it overlaps with our date range
            if week_end >= start_date_obj:
                # Adjust boundaries if needed
                effective_start = max(current_week_start, start_date_obj)
                effective_end = min(week_end, end_date_obj)
                
                weeks.append({
                    'start': effective_start,
                    'end': effective_end,
                    'key': week_end.strftime('%Y-%m-%d')  # Use Friday (end date) as week identifier
                })
            
            # Move to next Saturday
            current_week_start = current_week_start + timedelta(days=7)
            week_count += 1
        
        return weeks
    
    def _get_aggregated_stats_for_all_weeks(self, weeks: List[Dict], 
                                            start_date_obj: datetime, 
                                            end_date_obj: datetime) -> Dict:
        """
        Get aggregated stats for all weeks when we can't get individual sender data
        
        Args:
            weeks: List of week dictionaries
            start_date_obj: Start date
            end_date_obj: End date
            
        Returns:
            Dictionary with aggregated stats
        """
        aggregated_weekly_data = {}
        
        for week in weeks:
            week_start_iso = week['start'].strftime('%Y-%m-%dT00:00:00.000Z')
            week_end_iso = week['end'].strftime('%Y-%m-%dT23:59:59.999Z')
            
            logger.debug(f"Fetching aggregated stats for week {week['key']}")
            
            # Get stats with empty accountIds (all senders)
            stats = self.get_overall_stats(
                account_ids=[],  # Empty = all senders
                campaign_ids=[],
                start_date=week_start_iso,
                end_date=week_end_iso
            )
            
            if stats:
                # Extract metrics using the same helper function
                def get_field_value(stats_dict, *field_names, default=0):
                    for field_name in field_names:
                        if field_name in stats_dict:
                            value = stats_dict[field_name]
                            if value is not None:
                                return value
                    return default
                
                aggregated_weekly_data[week['key']] = {
                    'connections_sent': get_field_value(
                        stats, 'connectionRequestsSent', 'connectionRequests', 'connectionsSent', 
                        'invitesSent', 'sentConnections', 'totalConnectionsSent'
                    ),
                    'connections_accepted': get_field_value(
                        stats, 'connectionsAccepted', 'acceptedConnections', 'invitesAccepted', 
                        'acceptedInvites', 'totalConnectionsAccepted'
                    ),
                    'messages_sent': get_field_value(
                        stats, 'totalMessageStarted', 'messagesSent', 'sentMessages', 'totalMessagesSent', 'messages'
                    ),
                    'message_replies': get_field_value(
                        stats, 'repliesReceived', 'replies', 'messageReplies', 
                        'totalReplies', 'repliesCount'
                    ),
                    'open_conversations': get_field_value(
                        stats, 'openConversations', 'activeConversations', 
                        'conversations', 'activeChats'
                    ),
                    'interested': get_field_value(
                        stats, 'interested', 'interestedLeads', 
                        'leadsInterested', 'interestedCount'
                    ),
                    'leads_not_enrolled': get_field_value(
                        stats, 'leadsNotEnrolled', 'pendingLeads', 
                        'notEnrolled', 'pending'
                    )
                }
        
        # Format as if it's a single sender called "All Senders"
        result = {
            'start_date': start_date_obj.strftime('%Y-%m-%d'),
            'end_date': end_date_obj.strftime('%Y-%m-%d'),
            'senders': {
                'All Senders': []
            }
        }
        
        # Sort weeks and format
        sorted_weeks = sorted(aggregated_weekly_data.keys())
        for week_key in sorted_weeks:
            week_data = aggregated_weekly_data[week_key]
            connections_sent = week_data['connections_sent']
            connections_accepted = week_data['connections_accepted']
            messages_sent = week_data['messages_sent']
            message_replies = week_data['message_replies']
            
            acceptance_rate = (connections_accepted / connections_sent * 100) if connections_sent > 0 else 0
            reply_rate = (message_replies / messages_sent * 100) if messages_sent > 0 else 0
            
            result['senders']['All Senders'].append({
                'week_start': week_key,
                'connections_sent': connections_sent,
                'connections_accepted': connections_accepted,
                'acceptance_rate': round(acceptance_rate, 2),
                'messages_sent': messages_sent,
                'message_replies': message_replies,
                'reply_rate': round(reply_rate, 2),
                'open_conversations': week_data['open_conversations'],
                'interested': week_data['interested'],
                'leads_not_enrolled': week_data['leads_not_enrolled']
            })
        
        return result
    
    def get_overall_stats(self, account_ids: List[str] = None, campaign_ids: List[str] = None, 
                          start_date: str = None, end_date: str = None) -> Dict:
        """
        Get overall stats from HeyReach API using GetOverallStats endpoint
        
        Args:
            account_ids: List of LinkedIn account IDs. If None or empty, gets all senders
            campaign_ids: List of campaign IDs. If None or empty, gets all campaigns
            start_date: Start date in ISO format (YYYY-MM-DDTHH:MM:SS.000Z)
            end_date: End date in ISO format (YYYY-MM-DDTHH:MM:SS.000Z)
            
        Returns:
            Dictionary with overall stats
        """
        logger.info(f"Fetching overall stats from GetOverallStats endpoint...")
        
        endpoint = "api/public/stats/GetOverallStats"
        
        # Prepare request body
        # Convert account_ids to integers if they're strings
        processed_account_ids = []
        if account_ids:
            for acc_id in account_ids:
                try:
                    # Try to convert to int
                    if isinstance(acc_id, str):
                        processed_account_ids.append(int(acc_id))
                    else:
                        processed_account_ids.append(acc_id)
                except (ValueError, TypeError):
                    # If conversion fails, use as-is
                    processed_account_ids.append(acc_id)
        
        # Convert campaign_ids to integers if they're strings
        processed_campaign_ids = []
        if campaign_ids:
            for camp_id in campaign_ids:
                try:
                    if isinstance(camp_id, str):
                        processed_campaign_ids.append(int(camp_id))
                    else:
                        processed_campaign_ids.append(camp_id)
                except (ValueError, TypeError):
                    processed_campaign_ids.append(camp_id)
        
        request_data = {
            "accountIds": processed_account_ids if processed_account_ids else [],
            "campaignIds": processed_campaign_ids if processed_campaign_ids else [],
            "startDate": start_date,
            "endDate": end_date
        }
        
        logger.info(f"📡 GetOverallStats API Request: accountIds={processed_account_ids} (type: {[type(x).__name__ for x in processed_account_ids]}), campaignIds={processed_campaign_ids}, startDate={start_date}, endDate={end_date}")
        
        # Set headers according to HeyReach API documentation
        # The API documentation specifies Accept: text/plain
        headers = {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
            "Accept": "text/plain"  # API docs specify text/plain
        }
        
        try:
            response_data = self._make_request(endpoint, method="POST", data=request_data, headers=headers)
            
            # Log response for debugging
            if response_data:
                logger.debug(f"GetOverallStats raw response type: {type(response_data).__name__}")
                if isinstance(response_data, dict):
                    logger.debug(f"GetOverallStats raw response keys: {list(response_data.keys())}")
                elif isinstance(response_data, list):
                    logger.debug(f"GetOverallStats raw response is a list with {len(response_data)} items")
                else:
                    logger.debug(f"GetOverallStats raw response: {str(response_data)[:200]}")
            
            # Process response - HeyReach API returns {byDayStats: {...}, overallStats: {...}}
            # We want to use overallStats for aggregated weekly data, or aggregate byDayStats
            data = response_data
            if data and isinstance(data, dict):
                # HeyReach API structure: {byDayStats: {date: {...}}, overallStats: {...}}
                # Try to get overallStats first (aggregated data for the date range)
                if 'overallStats' in data and isinstance(data['overallStats'], dict) and len(data['overallStats']) > 0:
                    logger.debug("Found 'overallStats' - using aggregated data")
                    data = data['overallStats']
                    logger.info(f"Using overallStats with keys: {list(data.keys())}")
                # If no overallStats or it's empty, we need to aggregate from byDayStats
                elif 'byDayStats' in data and isinstance(data['byDayStats'], dict):
                    logger.debug("Found 'byDayStats' - will aggregate daily data")
                    # Aggregate daily stats for the week
                    by_day_stats = data['byDayStats']
                    aggregated = {
                        'connectionsSent': 0,
                        'connectionsAccepted': 0,
                        'messagesSent': 0,
                        'totalMessageReplies': 0,
                        'totalMessageStarted': 0,  # Open conversations
                        'totalInmailReplies': 0,
                        'inmailMessagesSent': 0
                    }
                    
                    # Sum up all daily stats
                    days_counted = 0
                    for date_key, day_stats in by_day_stats.items():
                        if isinstance(day_stats, dict):
                            days_counted += 1
                            aggregated['connectionsSent'] += int(day_stats.get('connectionsSent', 0) or 0)
                            aggregated['connectionsAccepted'] += int(day_stats.get('connectionsAccepted', 0) or 0)
                            aggregated['messagesSent'] += int(day_stats.get('messagesSent', 0) or 0)
                            aggregated['totalMessageReplies'] += int(day_stats.get('totalMessageReplies', 0) or 0)
                            aggregated['totalMessageStarted'] += int(day_stats.get('totalMessageStarted', 0) or 0)
                            aggregated['totalInmailReplies'] += int(day_stats.get('totalInmailReplies', 0) or 0)
                            aggregated['inmailMessagesSent'] += int(day_stats.get('inmailMessagesSent', 0) or 0)
                    
                    logger.info(f"Aggregated stats from {days_counted} days in byDayStats: connectionsSent={aggregated['connectionsSent']}, connectionsAccepted={aggregated['connectionsAccepted']}, totalMessageStarted={aggregated['totalMessageStarted']} (messages_sent), totalMessageReplies={aggregated['totalMessageReplies']}")
                    data = aggregated
                # Try other nested structures as fallback
                elif 'data' in data and isinstance(data['data'], dict):
                    logger.debug("Response has nested 'data' dict, using that")
                    data = data['data']
                elif 'result' in data and isinstance(data['result'], dict):
                    logger.debug("Response has nested 'result' key, using that")
                    data = data['result']
                elif 'stats' in data and isinstance(data['stats'], dict):
                    logger.debug("Response has nested 'stats' key, using that")
                    data = data['stats']
            elif isinstance(data, list) and len(data) > 0:
                logger.debug(f"Response is a list with {len(data)} items")
                if isinstance(data[0], dict):
                    data = data[0]
            
            # Log final processed structure
            if isinstance(data, dict):
                logger.debug(f"Final processed response keys: {list(data.keys())}")
                # Log key metrics
                if 'connectionsSent' in data or 'connectionsAccepted' in data:
                    logger.info(f"Final key metrics: connectionsSent={data.get('connectionsSent', 0)}, connectionsAccepted={data.get('connectionsAccepted', 0)}, totalMessageStarted={data.get('totalMessageStarted', 0)} (messages_sent), totalMessageReplies={data.get('totalMessageReplies', 0)}")
            
            return data if data else {}
        except Exception as e:
            logger.error(f"Error fetching overall stats: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {}
    
    def get_sender_weekly_performance(self, sender_id: str = None, start_date: str = None, 
                                     end_date: str = None) -> Dict:
        """
        Get weekly performance data for a specific sender or all senders using GetOverallStats API
        
        MEMORY-OPTIMIZED for Render free plan (512MB RAM limit):
        - Uses sequential batch processing instead of parallel ThreadPoolExecutor
        - Processes results immediately and clears raw data
        - Explicit garbage collection between batches
        
        Args:
            sender_id: Optional sender (LinkedIn account) ID. If None, gets all senders
            start_date: Start date (YYYY-MM-DD). If None, defaults to last 7 days
            end_date: End date (YYYY-MM-DD). If None, defaults to today
            
        Returns:
            Dictionary with weekly performance data grouped by sender
        """
        
        # Default to last 7 days if dates not provided
        if not end_date:
            end_date_obj = datetime.now()
        else:
            end_date_obj = datetime.strptime(end_date, '%Y-%m-%d')
        
        if not start_date:
            start_date_obj = end_date_obj - timedelta(days=7)
        else:
            start_date_obj = datetime.strptime(start_date, '%Y-%m-%d')
        
        # ALWAYS prioritize manually configured sender IDs if available
        linkedin_accounts = []
        
        # Use manually configured sender IDs if available (priority)
        if self.manual_sender_ids and len(self.manual_sender_ids) > 0:
            logger.info(f"Using {len(self.manual_sender_ids)} manually configured sender IDs")
            for sender_id_val in self.manual_sender_ids:
                sender_id_int = int(sender_id_val) if isinstance(sender_id_val, (str, float)) else sender_id_val
                sender_name = (
                    self.manual_sender_names.get(sender_id_int) or 
                    self.manual_sender_names.get(sender_id_val) or 
                    f'Sender {sender_id_val}'
                )
                linkedin_accounts.append({
                    'id': sender_id_val,
                    'linkedInUserListName': sender_name,
                    'name': sender_name
                })
            logger.info(f"Loaded {len(linkedin_accounts)} senders from manual configuration")
        else:
            # Fallback: try API if no manual config
            try:
                linkedin_accounts = self.get_linkedin_accounts(force_api=True)
                if linkedin_accounts:
                    logger.info(f"Found {len(linkedin_accounts)} LinkedIn accounts from API")
            except Exception as e:
                logger.debug(f"Could not fetch accounts from API: {e}")
        
        # Handle case where no accounts found
        if not linkedin_accounts:
            if sender_id and sender_id != 'all':
                logger.info(f"Using provided sender_id: {sender_id}")
                try:
                    sender_id_int = int(sender_id)
                    sender_name = self.manual_sender_names.get(sender_id_int, f'Sender {sender_id}')
                    linkedin_accounts = [{
                        'id': sender_id_int,
                        'linkedInUserListName': sender_name,
                        'name': sender_name
                    }]
                except (ValueError, TypeError):
                    logger.warning(f"Invalid sender_id format: {sender_id}")
                    return {
                        'start_date': start_date_obj.strftime('%Y-%m-%d'),
                        'end_date': end_date_obj.strftime('%Y-%m-%d'),
                        'senders': {}
                    }
            else:
                logger.warning("No LinkedIn accounts found.")
                return {
                    'start_date': start_date_obj.strftime('%Y-%m-%d'),
                    'end_date': end_date_obj.strftime('%Y-%m-%d'),
                    'senders': {}
                }
        
        # Filter by sender_id if a specific sender is requested (not 'all')
        if sender_id and sender_id != 'all' and linkedin_accounts:
            linkedin_accounts = [acc for acc in linkedin_accounts if str(acc.get('id')) == str(sender_id)]
            if not linkedin_accounts:
                logger.warning(f"Sender ID {sender_id} not found in available accounts")
                return {
                    'start_date': start_date_obj.strftime('%Y-%m-%d'),
                    'end_date': end_date_obj.strftime('%Y-%m-%d'),
                    'senders': {}
                }
        
        # Generate list of weeks (Saturday to Friday)
        weeks = []
        start_weekday = start_date_obj.weekday()
        
        # Calculate days to go back to get to Saturday
        if start_weekday == 5:  # Saturday
            days_back = 0
        elif start_weekday == 6:  # Sunday
            days_back = 1
        else:  # Monday (0) through Friday (4)
            days_back = start_weekday + 2
        
        first_saturday = start_date_obj - timedelta(days=days_back)
        current_week_start = first_saturday
        week_count = 0
        max_weeks = 52
        
        while current_week_start <= end_date_obj and week_count < max_weeks:
            week_end_friday = current_week_start + timedelta(days=6)  # Always Friday
            
            if week_end_friday >= start_date_obj:
                effective_start = max(current_week_start, start_date_obj)
                effective_end = min(week_end_friday, end_date_obj)
                
                weeks.append({
                    'start': effective_start,           # Actual data start (may be capped)
                    'end': effective_end,               # Actual data end (may be capped)
                    'friday': week_end_friday,          # Always the Friday of this week
                    'key': week_end_friday.strftime('%Y-%m-%d')  # Friday as week identifier
                })
            
            current_week_start = current_week_start + timedelta(days=7)
            week_count += 1
        
        # Store week objects by key for later formatting
        week_objects_by_key = {week['key']: week for week in weeks}
        
        # MEMORY-EFFICIENT: Use simple dict instead of defaultdict
        # Store only final processed data, not raw API responses
        sender_weekly_data = {}
        
        def get_field_value(stats_dict, *field_names, default=0):
            """Get field value, trying multiple field names"""
            if not isinstance(stats_dict, dict):
                return default
            for field_name in field_names:
                if field_name in stats_dict:
                    value = stats_dict[field_name]
                    if value is not None:
                        try:
                            return float(value) if isinstance(value, (int, float, str)) else default
                        except (ValueError, TypeError):
                            return default
            return default
        
        def get_sender_name(account):
            """Get sender name with proper fallback"""
            account_id = account.get('id')
            account_id_int = int(account_id) if account_id and isinstance(account_id, (str, float)) else account_id
            
            sender_name = None
            if account_id_int:
                sender_name = self.manual_sender_names.get(account_id_int)
            if not sender_name and account_id:
                sender_name = self.manual_sender_names.get(account_id)
            if not sender_name:
                sender_name = account.get('linkedInUserListName') or account.get('name')
            if not sender_name:
                sender_name = f"Sender {account_id}"
            return sender_name, account_id_int if account_id_int else account_id
        
        # Create task list
        tasks = [(account, week) for account in linkedin_accounts for week in weeks]
        total_tasks = len(tasks)
        
        logger.info(f"Processing {total_tasks} sender-week combinations (memory-optimized sequential mode)...")
        
        # MEMORY-OPTIMIZED: Process in small sequential batches
        # Instead of ThreadPoolExecutor which creates all futures at once
        batch_size = 3  # Very small batches for memory efficiency
        completed = 0
        first_result_logged = False
        rate_limit_errors = 0
        base_delay = 0.1  # Base delay between API calls (seconds)
        
        for i in range(0, total_tasks, batch_size):
            batch = tasks[i:i + batch_size]
            
            for account, week in batch:
                completed += 1
                sender_name, api_account_id = get_sender_name(account)
                
                week_start_iso = week['start'].strftime('%Y-%m-%dT00:00:00.000Z')
                week_end_iso = week['end'].strftime('%Y-%m-%dT23:59:59.999Z')
                
                # Make API call with retry logic
                stats = None
                max_retries = 2  # Reduced retries for speed
                
                for attempt in range(max_retries):
                    try:
                        stats = self.get_overall_stats(
                            account_ids=[api_account_id],
                            campaign_ids=[],
                            start_date=week_start_iso,
                            end_date=week_end_iso
                        )
                        rate_limit_errors = max(0, rate_limit_errors - 1)
                        break
                    except Exception as e:
                        error_str = str(e)
                        if '429' in error_str or 'rate limit' in error_str.lower():
                            rate_limit_errors += 1
                            if attempt < max_retries - 1:
                                wait_time = base_delay * (2 ** (attempt + rate_limit_errors))
                                wait_time = min(wait_time, 5.0)  # Cap at 5 seconds
                                time.sleep(wait_time)
                                continue
                        logger.warning(f"API error for {sender_name}: {str(e)[:100]}")
                        stats = {}
                        break
                
                # Log first result structure
                if not first_result_logged and stats and isinstance(stats, dict) and len(stats) > 0:
                    logger.info(f"📊 First API response keys: {list(stats.keys())}")
                    first_result_logged = True
                
                # MEMORY-EFFICIENT: Process and store only essential data immediately
                if stats and isinstance(stats, dict):
                    if sender_name not in sender_weekly_data:
                        sender_weekly_data[sender_name] = {}
                    
                    sender_weekly_data[sender_name][week['key']] = {
                        'connections_sent': get_field_value(stats, 'connectionsSent', 'connectionRequestsSent'),
                        'connections_accepted': get_field_value(stats, 'connectionsAccepted', 'acceptedConnections'),
                        'messages_sent': get_field_value(stats, 'totalMessageStarted', 'messagesSent'),
                        'message_replies': get_field_value(stats, 'totalMessageReplies', 'repliesReceived'),
                        'open_conversations': get_field_value(stats, 'openConversations', 'totalMessageStarted'),
                        'interested': get_field_value(stats, 'interested', 'interestedLeads'),
                        'leads_not_enrolled': get_field_value(stats, 'leadsNotEnrolled', 'pendingLeads')
                    }
                
                # Clear the stats dict reference
                stats = None
                
                # Small delay to avoid rate limiting
                if rate_limit_errors > 3:
                    time.sleep(base_delay * 2)
                else:
                    time.sleep(base_delay)
                
                # Log progress every 20 tasks
                if completed % 20 == 0 or completed == total_tasks:
                    logger.info(f"Processed {completed}/{total_tasks} API calls...")
            
            # CRITICAL: Force garbage collection after each batch
            gc.collect()
        
        logger.info(f"Completed processing all {total_tasks} sender-week combinations")
        
        # Format data for response - group by client if client groups are available
        result = {
            'start_date': start_date_obj.strftime('%Y-%m-%d'),
            'end_date': end_date_obj.strftime('%Y-%m-%d'),
            'senders': {},
            'clients': {}  # Group by client
        }
        
        # Helper function to format weeks data
        def format_weeks_data(weekly_data_dict, week_objects_dict=None):
            """Format weekly data into the response format"""
            sorted_weeks = sorted(weekly_data_dict.keys())
            formatted_weeks = []
            for week_key in sorted_weeks:
                week_data = weekly_data_dict[week_key]
                connections_sent = week_data.get('connections_sent', 0) or 0
                connections_accepted = week_data.get('connections_accepted', 0) or 0
                messages_sent = week_data.get('messages_sent', 0) or 0
                message_replies = week_data.get('message_replies', 0) or 0
                
                acceptance_rate = (connections_accepted / connections_sent * 100) if connections_sent > 0 else 0
                reply_rate = (message_replies / messages_sent * 100) if messages_sent > 0 else 0
                
                # week_key is ALWAYS the Friday date (end of Sat-Fri week)
                # Use this for display regardless of user's date range
                week_end_str = week_key  # Friday - ALWAYS use this for display
                week_start_str = week_key  # Default
                
                if week_objects_dict and week_key in week_objects_dict:
                    week_obj = week_objects_dict[week_key]
                    # Use the actual Saturday start for week_start
                    # But ALWAYS use the Friday for week_end (for display)
                    if 'friday' in week_obj:
                        week_start_str = (week_obj['friday'] - timedelta(days=6)).strftime('%Y-%m-%d')
                        week_end_str = week_obj['friday'].strftime('%Y-%m-%d')
                    else:
                        week_start_str = week_obj['start'].strftime('%Y-%m-%d')
                        week_end_str = week_key  # Fallback to key which is Friday
                
                formatted_weeks.append({
                    'week_start': week_start_str,  # Saturday (start of Sat-Fri week)
                    'week_end': week_end_str,      # Friday (end of Sat-Fri week) - ALWAYS the Friday
                    'connections_sent': int(connections_sent),
                    'connections_accepted': int(connections_accepted),
                    'acceptance_rate': round(acceptance_rate, 2),
                    'messages_sent': int(messages_sent),
                    'message_replies': int(message_replies),
                    'reply_rate': round(reply_rate, 2),
                    # open_conversations and interested default to 0 unless Supabase/AI is configured
                    # These will be populated from Supabase AI evaluation if configured
                    'open_conversations': 0,  # Default to 0, will be updated from Supabase if configured
                    'interested': 0,  # Default to 0, will be updated from Supabase if configured
                    'leads_not_enrolled': int(week_data.get('leads_not_enrolled', 0) or 0)
                })
            return formatted_weeks
        
        # Organize senders by client
        senders_by_client = {}
        senders_without_client = []
        
        for sender_name, weekly_data in sender_weekly_data.items():
            # Find which client this sender belongs to
            sender_client = None
            for account in linkedin_accounts:
                account_name = account.get('linkedInUserListName') or account.get('name')
                if account_name == sender_name:
                    account_id = account.get('id')
                    sender_client = self.sender_to_client.get(account_id)
                    if sender_client:
                        break
            
            if sender_client:
                if sender_client not in senders_by_client:
                    senders_by_client[sender_client] = {}
                senders_by_client[sender_client][sender_name] = weekly_data
            else:
                senders_without_client.append((sender_name, weekly_data))
        
        # Process senders grouped by client
        for client_name, client_senders in senders_by_client.items():
            result['clients'][client_name] = {}
            
            for sender_name, weekly_data in client_senders.items():
                formatted_weeks = format_weeks_data(weekly_data, week_objects_by_key)
                result['clients'][client_name][sender_name] = formatted_weeks
                # Also add to main senders dict for backward compatibility
                # Only add if not already present (to avoid duplicates)
                if sender_name not in result['senders']:
                    result['senders'][sender_name] = formatted_weeks
        
        # Process senders without a client
        for sender_name, weekly_data in senders_without_client:
            result['senders'][sender_name] = format_weeks_data(weekly_data, week_objects_by_key)
        
        # MEMORY CLEANUP: Clear intermediate data structures
        sender_weekly_data.clear()
        senders_by_client.clear()
        senders_without_client.clear()
        gc.collect()
        
        # Log summary
        logger.info(f"📊 Formatted data: {len(result['senders'])} senders, {len(result['clients'])} clients")
        
        return result
    
    def get_sender_daily_performance(self, sender_id: str = None,
                                     start_date: str = None, end_date: str = None) -> Dict:
        """
        Get DAILY performance breakdown for a specific sender (or all senders).

        Returns per-day metrics from HeyReach's byDayStats. Much finer-grained than
        the weekly Sat-Fri aggregation.

        Args:
            sender_id: Optional sender (LinkedIn account) ID. If None, gets all senders.
            start_date: YYYY-MM-DD
            end_date: YYYY-MM-DD

        Returns:
            {
                'start_date': str,
                'end_date': str,
                'senders': {
                    sender_name: [
                        {date, connections_sent, connections_accepted, acceptance_rate,
                         messages_sent, message_replies, reply_rate}, ...
                    ],
                    ...
                },
                'totals_by_date': { date: {sent, accepted, messages, replies}, ... }
            }
        """
        if not end_date:
            end_obj = datetime.now()
        else:
            end_obj = datetime.strptime(end_date, '%Y-%m-%d')
        if not start_date:
            start_obj = end_obj - timedelta(days=28)
        else:
            start_obj = datetime.strptime(start_date, '%Y-%m-%d')

        start_iso = start_obj.strftime('%Y-%m-%dT00:00:00.000Z')
        end_iso = end_obj.strftime('%Y-%m-%dT23:59:59.999Z')

        # Build sender list (same priority as weekly: env config > API)
        linkedin_accounts = []
        if self.manual_sender_ids and len(self.manual_sender_ids) > 0:
            for sid in self.manual_sender_ids:
                sid_int = int(sid) if isinstance(sid, (str, float)) else sid
                name = self.manual_sender_names.get(sid_int) or self.manual_sender_names.get(sid) or f'Sender {sid}'
                linkedin_accounts.append({'id': sid_int, 'name': name})
        else:
            try:
                accounts = self.get_linkedin_accounts(force_api=True) or []
                for acc in accounts:
                    aid = acc.get('id')
                    aid_int = int(aid) if aid and isinstance(aid, (str, float)) else aid
                    name = (
                        self.manual_sender_names.get(aid_int)
                        or self.manual_sender_names.get(aid)
                        or acc.get('linkedInUserListName')
                        or acc.get('name')
                        or f'Sender {aid}'
                    )
                    linkedin_accounts.append({'id': aid_int, 'name': name})
            except Exception as e:
                logger.warning(f"Could not fetch LinkedIn accounts: {e}")

        # Filter to specific sender if requested
        if sender_id and sender_id != 'all':
            try:
                target = int(sender_id)
                linkedin_accounts = [a for a in linkedin_accounts if a.get('id') == target]
            except (ValueError, TypeError):
                pass

        endpoint = "api/public/stats/GetOverallStats"
        headers = {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
            "Accept": "text/plain",
        }

        senders_out = {}
        totals_by_date = {}

        for account in linkedin_accounts:
            aid = account.get('id')
            name = account.get('name')
            request_data = {
                "accountIds": [int(aid)] if aid is not None else [],
                "campaignIds": [],
                "startDate": start_iso,
                "endDate": end_iso,
            }
            try:
                resp = self._make_request(endpoint, method="POST", data=request_data, headers=headers) or {}
            except Exception as e:
                logger.warning(f"Daily stats failed for {name}: {e}")
                resp = {}

            by_day = (resp or {}).get('byDayStats') or {}
            day_rows = []
            for date_key, stats in sorted(by_day.items()):
                if not isinstance(stats, dict):
                    continue
                sent = int(stats.get('connectionsSent') or 0)
                accepted = int(stats.get('connectionsAccepted') or 0)
                messages = int(stats.get('messagesSent') or stats.get('totalMessageStarted') or 0)
                replies = int(stats.get('totalMessageReplies') or 0)
                acc_rate = round((accepted / sent * 100), 2) if sent > 0 else 0
                reply_rate = round((replies / messages * 100), 2) if messages > 0 else 0
                day_rows.append({
                    'date': date_key[:10],
                    'connections_sent': sent,
                    'connections_accepted': accepted,
                    'acceptance_rate': acc_rate,
                    'messages_sent': messages,
                    'message_replies': replies,
                    'reply_rate': reply_rate,
                })
                d = date_key[:10]
                if d not in totals_by_date:
                    totals_by_date[d] = {'sent': 0, 'accepted': 0, 'messages': 0, 'replies': 0}
                totals_by_date[d]['sent'] += sent
                totals_by_date[d]['accepted'] += accepted
                totals_by_date[d]['messages'] += messages
                totals_by_date[d]['replies'] += replies

            if day_rows:
                senders_out[name] = day_rows

            time.sleep(0.05)

        return {
            'start_date': start_obj.strftime('%Y-%m-%d'),
            'end_date': end_obj.strftime('%Y-%m-%d'),
            'senders': senders_out,
            'totals_by_date': totals_by_date,
        }

    def get_campaigns_with_stats(self, start_date: str = None, end_date: str = None,
                                 sender_id: str = None, max_workers: int = 4) -> List[Dict]:
        """
        Get all campaigns enriched with aggregated per-campaign stats for the given date range.

        Uses a small thread pool to parallelize the per-campaign stats requests
        (rate-limit-friendly, max_workers=4 by default).

        Args:
            start_date: YYYY-MM-DD
            end_date: YYYY-MM-DD
            sender_id: optional sender (LinkedIn account) ID to filter stats by
            max_workers: parallelism for per-campaign stats fetches

        Returns:
            List of dicts with: id, name, audience, status, started_at, sent, accepted,
            messages, replies, acceptanceRate, replyRate, totalLeads, leadsInProgress,
            leadsFinished
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        campaigns = self.get_campaigns()
        if not campaigns:
            return []

        # Default: last 28 days
        if not end_date:
            end_obj = datetime.now()
        else:
            end_obj = datetime.strptime(end_date, '%Y-%m-%d')
        if not start_date:
            start_obj = end_obj - timedelta(days=28)
        else:
            start_obj = datetime.strptime(start_date, '%Y-%m-%d')

        start_iso = start_obj.strftime('%Y-%m-%dT00:00:00.000Z')
        end_iso = end_obj.strftime('%Y-%m-%dT23:59:59.999Z')

        sender_account_ids = []
        if sender_id and sender_id != 'all':
            try:
                sender_account_ids = [int(sender_id)]
            except (ValueError, TypeError):
                pass

        def fetch_one(camp):
            camp_id = camp.get('id')
            if camp_id is None:
                return None
            try:
                camp_id_int = int(camp_id)
            except (ValueError, TypeError):
                camp_id_int = camp_id

            stats = {}
            try:
                stats = self.get_overall_stats(
                    account_ids=sender_account_ids,
                    campaign_ids=[camp_id_int],
                    start_date=start_iso,
                    end_date=end_iso,
                ) or {}
            except Exception as e:
                logger.warning(f"Stats fetch failed for campaign {camp_id}: {e}")

            sent = int(stats.get('connectionsSent') or stats.get('connectionRequestsSent') or 0)
            accepted = int(stats.get('connectionsAccepted') or stats.get('acceptedConnections') or 0)
            messages = int(stats.get('totalMessageStarted') or stats.get('messagesSent') or 0)
            replies = int(stats.get('totalMessageReplies') or stats.get('repliesReceived') or 0)

            acceptance_rate = round((accepted / sent * 100), 2) if sent > 0 else 0
            reply_rate = round((replies / messages * 100), 2) if messages > 0 else 0

            progress = camp.get('progressStats') or {}

            return {
                'id': camp_id,
                'name': camp.get('name') or camp.get('campaignName') or f'Campaign {camp_id}',
                'audience': camp.get('linkedInUserListName') or '',
                'status': camp.get('status') or 'unknown',
                'started_at': camp.get('startedAt') or camp.get('creationTime') or None,
                'sent': sent,
                'accepted': accepted,
                'messages': messages,
                'replies': replies,
                'acceptanceRate': acceptance_rate,
                'replyRate': reply_rate,
                'totalLeads': int(progress.get('totalUsers') or 0),
                'leadsInProgress': int(progress.get('totalUsersInProgress') or 0),
                'leadsFinished': int(progress.get('totalUsersFinished') or 0),
            }

        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(fetch_one, c) for c in campaigns]
            for f in as_completed(futures):
                try:
                    r = f.result()
                    if r:
                        results.append(r)
                except Exception as e:
                    logger.warning(f"Per-campaign future failed: {e}")
        return results

    def test_connection(self) -> bool:
        """
        Test API connection

        Returns:
            True if connection successful, False otherwise
        """
        try:
            campaigns = self.get_campaigns()
            logger.info("✅ HeyReach API connection successful")
            return True
        except Exception as e:
            logger.error(f"❌ HeyReach API connection failed: {e}")
            return False
