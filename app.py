import os
import requests
import logging
import re
import time
import json
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from web3 import Web3
from bs4 import BeautifulSoup
import json


# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger()


# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Neynar API Configuration
NEYNAR_API_KEY = os.getenv("NEYNAR_API_KEY")
BOT_USERNAME = os.getenv("BOT_USERNAME")

# Web3 Configuration
RPC_URL = 'https://mainnet.base.org'  # Replace with your actual RPC URL
web3 = Web3(Web3.HTTPProvider(RPC_URL))

if not web3.is_connected():
    logger.error("Error: Unable to connect to the Web3 provider.")
    exit()

# Uniswap V3 Router Address and ABI
router_address = web3.to_checksum_address("0xE592427A0AEce92De3Edee1F18E0157C05861564")  # Uniswap V3 Router
token_out = web3.to_checksum_address("0x4200000000000000000000000000000000000006")  # USDC on Ethereum
router_abi = json.loads("""[
  {
    "inputs": [
      {"name": "params", "type": "tuple", "components": [
        {"name": "tokenIn", "type": "address"},
        {"name": "tokenOut", "type": "address"},
        {"name": "fee", "type": "uint24"},
        {"name": "recipient", "type": "address"},
        {"name": "deadline", "type": "uint256"},
        {"name": "amountIn", "type": "uint256"},
        {"name": "amountOutMinimum", "type": "uint256"},
        {"name": "sqrtPriceLimitX96", "type": "uint160"}
      ]}
    ],
    "name": "exactInputSingle",
    "outputs": [{"name": "amountOut", "type": "uint256"}],
    "stateMutability": "payable",
    "type": "function"
  }
]""")

erc20_abi = [
    {
        "constant": False,
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "payable": False,
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    }
]


router_contract = web3.eth.contract(address=router_address, abi=router_abi)

# Wallet details (replace with your wallet address and private key)
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS")  # Replace or set in .env
PRIVATE_KEY = os.getenv("PRIVATE_KEY")  # Replace or set in .env

if not WALLET_ADDRESS or not PRIVATE_KEY:
    logger.error("Wallet address or private key is not set.")
    exit()

wallet = (WALLET_ADDRESS, PRIVATE_KEY)

# Define Token Details (Dynamic)
weth_address = web3.to_checksum_address("0x4200000000000000000000000000000000000006")  # WETH address on Base
token_out_address = None  # Dynamically set later
fee_tier = 10000  # Default fee tier for Uniswap V3 pools


referral_code = '7679027761'

# Load environment variables
DEBUG_SNIPING = os.getenv("DEBUG_SNIPING", "false").lower() == "true"


# Amount to swap in wei (set your desired amount here)
DEFAULT_SWAP_AMOUNT = web3.to_wei(0.0001, 'ether')  # Example: 0.01 ETH

app = Flask(__name__)

def evaluate_contract(contract_address):
    """
    Evaluate if a contract meets the sniping requirements.
    Args:
        contract_address (str): The contract address to evaluate.
    Returns:
        bool: True if the contract meets the requirements, False otherwise.
    """
    try:
        # Example criteria: market cap, token liquidity, etc.
        market_cap = fetch_market_cap(contract_address)
        if market_cap and market_cap > 1_000_000:  # Example: Minimum market cap of $1M
            logger.info(f"Contract {contract_address} meets the sniping requirements. Market Cap: ${market_cap:.2f}")
            return True
        else:
            logger.warning(f"Contract {contract_address} does not meet the sniping requirements.")
            return False
    except Exception as e:
        logger.error(f"Error evaluating contract {contract_address}: {e}")
        return False

# Function to send a message or photo to Telegram




# Function to fetch user information using the Neynar API
def fetch_user_info(fid):
    url = "https://api.neynar.com/v2/farcaster/user/bulk"
    headers = {
        "accept": "application/json",
        "x-neynar-experimental": "true",
        "x-api-key": NEYNAR_API_KEY
    }
    params = {"fids": fid}  # Query the specific fid
    try:
        response = requests.get(url, headers=headers, params=params)
        logger.debug(f"Neynar API Response: {response.status_code} - {response.text}")
        if response.status_code == 200:
            data = response.json()
            if "users" in data and len(data["users"]) > 0:
                return data["users"][0]  # Return the first user object
            else:
                logger.error(f"No user data found for fid {fid}")
                return None
        else:
            logger.error(f"Failed to fetch user info for fid {fid}: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Error fetching user info for fid {fid}: {e}")
        return None
    
def fetch_cast_by_hash(cast_hash):
    url = f"https://api.neynar.com/v2/farcaster/cast/{cast_hash}"
    headers = {
        "accept": "application/json",
        "x-api-key": NEYNAR_API_KEY
    }
    try:
        response = requests.get(url, headers=headers)
        logger.debug(f"Neynar API Response for cast {cast_hash}: {response.status_code} - {response.text}")
        if response.status_code == 200:
            data = response.json()
            return data.get('data', {})
        else:
            logger.error(f"Failed to fetch cast for hash {cast_hash}: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Error fetching cast for hash {cast_hash}: {e}")
        return None
        
def extract_image_url_from_cast(cast_data):
    # Try to extract image URL from 'embeds' field
    embeds = cast_data.get('embeds', [])
    for embed in embeds:
        if embed.get('type') == 'image' and 'url' in embed:
            return embed['url']
    # Alternatively, check for 'attachments' or other fields
    attachments = cast_data.get('attachments', [])
    for attachment in attachments:
        if attachment.get('type') == 'image' and 'url' in attachment:
            return attachment['url']
    # If no image found, return None
    return None
   

def get_follower_rating(follower_count, use_emojis=False):
    """
    Returns a star or emoji rating based on follower count.
    :param follower_count: The number of followers.
    :param use_emojis: Whether to use emoji-based ratings.
    :return: A string representing the rating.
    """
    if follower_count is None or follower_count == "N/A":
        return "💩" if use_emojis else "✰✰✰✰✰"
    
    try:
        count = int(follower_count)
        if count < 50:
            return "💩" if use_emojis else "💩"
        elif count < 100:
            return "⭐✰✰✰✰" if use_emojis else "⭐✰✰✰✰"
        elif count < 500:
            return "⭐⭐✰✰✰" if use_emojis else "⭐⭐✰✰✰"
        elif count < 1000:
            return "⭐⭐⭐✰✰" if use_emojis else "⭐⭐⭐✰✰"
        elif count < 5000:  # Updated to 3 stars for 1000-5000
            return "⭐⭐⭐✰✰" if use_emojis else "⭐⭐⭐✰✰"
        elif count < 20000:  # 4 stars for 5000-20000
            return "⭐⭐⭐⭐✰" if use_emojis else "⭐⭐⭐⭐✰"
        else:  # 5 stars for 20000 and above
            return "⭐⭐⭐⭐⭐" if use_emojis else "⭐⭐⭐⭐⭐"
    except ValueError:
        return "💩" if use_emojis else "✰✰✰✰✰"


def format_follower_count(follower_count):
    """Formats the follower count with commas for better readability."""
    try:
        count = int(follower_count)  # Convert to an integer
        return "{:,}".format(count)  # Add commas
    except (ValueError, TypeError):
        return "N/A"  # Return "N/A" if invalid

def get_rating_emoji(rating):
    if rating is None or rating == "N/A":
        return ""
    try:
        rating = float(rating)
        if rating < 0.3:
            return "⭐"
        elif rating < 0.6:
            return "⭐⭐"
        else:
            return "⭐⭐⭐"
    except ValueError:
        return ""

def extract_clanker_url(message_text):
    pattern = r'(https://clanker\.world/clanker/[a-zA-Z0-9]+)'
    match = re.search(pattern, message_text)
    if match:
        return match.group(1)
    else:
        return None

def extract_clanker_address(clanker_url):
    if clanker_url:
        # Assuming the contract address is at the end of the URL
        parts = clanker_url.strip().split('/')
        if len(parts) >= 5:
            return parts[-1]
    return None

def html_escape(text):
    if text:
        text = text.replace('&', '&amp;')
        text = text.replace('<', '&lt;')
        text = text.replace('>', '&gt;')
        text = text.replace('"', '&quot;')
        text = text.replace("'", '&#39;')
    return text

def get_token_info(token_address):
    """
    Fetch token information (symbol, decimals) from the blockchain.
    Args:
        token_address (str): The token's contract address.
    Returns:
        dict: A dictionary with token information, or None if it fails.
    """
    try:
        token_contract = web3.eth.contract(address=token_address, abi=erc20_abi)
        symbol = token_contract.functions.symbol().call()
        decimals = token_contract.functions.decimals().call()
        return {"symbol": symbol, "decimals": decimals}
    except Exception as e:
        logger.error(f"Error fetching token info for {token_address}: {e}")
        return None

# Function to swap tokens
def swap_tokens_v3(wallet, router_contract, token_in, token_out, fee_tier, amount_in):
    address, private_key = wallet

    try:
        # Fetch token_in details
        token_in_info = get_token_info(token_in)
        if not token_in_info:
            logger.error("Invalid token_in. Cannot perform swap.")
            return

        logger.info(f"Swapping {amount_in} {token_in_info['symbol']} for {token_out}.")

        # Approve router to spend token_in
        token_in_contract = web3.eth.contract(address=token_in, abi=erc20_abi)
        approve_tx = token_in_contract.functions.approve(router_address, amount_in).build_transaction({
            'from': address,
            'gas': 50000,
            'gasPrice': web3.eth.gas_price,
            'nonce': web3.eth.get_transaction_count(address, 'pending'),
        })
        signed_approve_tx = web3.eth.account.sign_transaction(approve_tx, private_key)
        approve_tx_hash = web3.eth.send_raw_transaction(signed_approve_tx.raw_transaction)
        logger.info(f"Approval transaction hash: {approve_tx_hash.hex()}")

        # Wait for approval confirmation
        web3.eth.wait_for_transaction_receipt(approve_tx_hash)

        # Build swap parameters
        params = {
            "tokenIn": token_in,
            "tokenOut": token_out,
            "fee": fee_tier,
            "recipient": address,
            "deadline": int(time.time()) + 300,  # 5 minutes deadline
            "amountIn": amount_in,
            "amountOutMinimum": 1,  # Accept any positive amount
            "sqrtPriceLimitX96": 0  # No price limit
        }

        # Build and send the swap transaction
        tx = router_contract.functions.exactInputSingle(params).build_transaction({
            'from': address,
            'gas': 210000,
            'gasPrice': web3.eth.gas_price,
            'nonce': web3.eth.get_transaction_count(address, 'pending'),
        })
        signed_tx = web3.eth.account.sign_transaction(tx, private_key)
        tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)
        logger.info(f"Swap transaction hash: {tx_hash.hex()}")

        # Wait for swap confirmation
        receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
        logger.info(f"Swap transaction confirmed. Receipt: {receipt}")

    except Exception as e:
        logger.error(f"Error during Uniswap V3 swap: {e}")




# Define the specific user fid to monitor
MONITORED_FID = "411466"  # Replace with the desired fid
FOLLOWER_THRESHOLD = 1   # Set the follower count threshold


def fetch_clanker_image(clanker_url):
    """
    Scrapes the Clanker URL to fetch the token's image URL.

    Args:
        clanker_url (str): The URL to the Clanker page.

    Returns:
        str: The image URL if found, or None otherwise.
    """
    try:
        response = requests.get(clanker_url, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            # Locate the image element based on its structure in the HTML
            img_tag = soup.find('img', {'class': 'w-full h-full object-contain rounded-lg bg-gray-50'})
            if img_tag and 'src' in img_tag.attrs:
                return img_tag['src']
        logger.error(f"No valid image found on Clanker page: {clanker_url}")
    except Exception as e:
        logger.error(f"Error fetching Clanker image: {e}")
    return None


def fetch_clanker_ticker(clanker_url):
    """
    Scrapes the Clanker URL to fetch the token's ticker.

    Args:
        clanker_url (str): The URL to the Clanker page.

    Returns:
        str: The ticker if found, or None otherwise.
    """
    try:
        response = requests.get(clanker_url, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            # Locate the ticker element based on its structure in the HTML
            ticker_tag = soup.find('p', {'class': 'text-sm font-medium text-gray-500'})
            if ticker_tag:
                return ticker_tag.text.strip()
        logger.error(f"No valid ticker found on Clanker page: {clanker_url}")
    except Exception as e:
        logger.error(f"Error fetching Clanker ticker: {e}")
    return None


def extract_ticker(message_text):
    """
    Extracts the ticker name from the message text.
    The ticker always starts with a $.

    Args:
        message_text (str): The message text to parse.

    Returns:
        str: The extracted ticker name, or None if not found.
    """
    match = re.search(r"\$\w+", message_text)
    return match.group(0) if match else None

def truncate_message(message, max_length=100):
    """
    Truncates a message to a specified maximum length, appending '...' if it exceeds the limit.
    
    Args:
        message (str): The message to truncate.
        max_length (int): The maximum allowed length of the message.
        
    Returns:
        str: The truncated message.
    """
    return message[:max_length] + '...' if len(message) > max_length else message

def fetch_market_cap(contract_address):
    try:
        api_url = f"https://api.dexscreener.com/latest/dex/tokens/{contract_address}"
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
        data = response.json()

        # Extract price from the API response
        price_usd = float(data['pairs'][0]['priceUsd'])
        total_supply = 1_000_000_000  # Adjust as needed
        market_cap = total_supply * price_usd

        logger.info(f"Market Cap for {contract_address}: ${market_cap:.2f}")
        return market_cap
    except Exception as e:
        logger.error(f"Error fetching market cap for {contract_address}: {e}")
        return None

processed_events = set()  # Global set to track processed events

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.json
        logger.debug("Received webhook request.")

        # Extract relevant data from webhook payload
        cast_data = data.get("data", {})
        cast_hash = cast_data.get("hash")  # Unique identifier for the cast
        if not cast_hash:
            logger.error("No cast hash in webhook data.")
            return jsonify({"status": "Invalid data"}), 400

        # Deduplication: Check if this event has already been processed
        if cast_hash in processed_events:
            logger.info(f"Duplicate event detected: {cast_hash}. Skipping.")
            return jsonify({"status": "Duplicate event"}), 200

        # Mark the event as processed
        processed_events.add(cast_hash)

        # Extract the author's fid and message text
        author_fid = str(cast_data.get("author", {}).get("fid", ""))
        original_message_text = cast_data.get("text", "")
        logger.debug(f"Author FID: {author_fid}")
        logger.debug(f"Message Text: {original_message_text}")

        # Ensure the message contains the required keyword
        if "countdown" not in original_message_text.lower():
            logger.info("Original message does not contain the required keyword. Ignored.")
            return jsonify({"status": "No matching message"}), 200

        # Fetch parent user info using Neynar API
        parent_author_fid = cast_data.get("parent_author", {}).get("fid", None) or "N/A"
        parent_user_info = fetch_user_info(parent_author_fid) if parent_author_fid != "N/A" else None
        if not parent_user_info:
            logger.warning(f"User information for fid {parent_author_fid} could not be retrieved.")
            return jsonify({"status": "User info unavailable"}), 400

        username = parent_user_info.get("username", "N/A")
        follower_count = parent_user_info.get("follower_count", 0)  # Ensure follower count defaults to 0
        logger.info(f"Username: {username}, Follower Count: {follower_count}")

        # Check if the follower count meets the threshold
        FOLLOWER_THRESHOLD = 1000  # Replace with your desired threshold
        if int(follower_count) < FOLLOWER_THRESHOLD:
            logger.info(f"Follower count ({follower_count}) below threshold ({FOLLOWER_THRESHOLD}). Ignored.")
            return jsonify({"status": "Follower count below threshold"}), 200

        # Extract Clanker World URL and contract address
        clanker_url = extract_clanker_url(original_message_text)
        clanker_address = extract_clanker_address(clanker_url)
        if not clanker_address:
            logger.error("No valid contract address found in the message.")
            return jsonify({"status": "No valid contract address"}), 400

        logger.info(f"Clanker Address: {clanker_address}")

        # Validate the contract and initiate the purchase
        if evaluate_contract(clanker_address):  # Ensure the contract meets criteria
            logger.info(f"Contract {clanker_address} meets requirements. Initiating token purchase...")
            swap_tokens_v3(
                wallet=wallet,
                router_contract=router_contract,
                token_in=weth_address,
                token_out=clanker_address,
                fee_tier=fee_tier,
                amount_in=DEFAULT_SWAP_AMOUNT
            )
        else:
            logger.warning(f"Contract {clanker_address} does not meet the sniping criteria.")
            return jsonify({"status": "Contract did not meet criteria"}), 400

        return jsonify({"status": "Purchase initiated"}), 200

    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return jsonify({"status": "Error processing webhook"}), 500


# Updated swap function with additional safety checks
def swap_tokens_v3(wallet, router_contract, token_in, token_out, fee_tier, amount_in):
    address, private_key = wallet
    try:
        # Approve router to spend token_in
        token_in_contract = web3.eth.contract(address=token_in, abi=erc20_abi)
        allowance = token_in_contract.functions.allowance(address, router_address).call()
        if allowance < amount_in:
            approve_tx = token_in_contract.functions.approve(router_address, amount_in).build_transaction({
                'from': address,
                'gas': 50000,
                'gasPrice': web3.eth.gas_price,
                'nonce': web3.eth.get_transaction_count(address, 'pending'),
            })
            signed_approve_tx = web3.eth.account.sign_transaction(approve_tx, private_key)
            approve_tx_hash = web3.eth.send_raw_transaction(signed_approve_tx.raw_transaction)
            logger.info(f"Approval transaction sent. Hash: {approve_tx_hash.hex()}")
            web3.eth.wait_for_transaction_receipt(approve_tx_hash)
        
        # Build and send the swap transaction
        params = {
            "tokenIn": token_in,
            "tokenOut": token_out,
            "fee": fee_tier,
            "recipient": address,
            "deadline": int(time.time()) + 300,
            "amountIn": amount_in,
            "amountOutMinimum": 1,
            "sqrtPriceLimitX96": 0
        }
        tx = router_contract.functions.exactInputSingle(params).build_transaction({
            'from': address,
            'gas': 210000,
            'gasPrice': web3.eth.gas_price,
            'nonce': web3.eth.get_transaction_count(address, 'pending'),
        })
        signed_tx = web3.eth.account.sign_transaction(tx, private_key)
        tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)
        logger.info(f"Swap transaction sent. Hash: {tx_hash.hex()}")

        # Wait for confirmation
        receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
        logger.info(f"Transaction confirmed. Receipt: {receipt}")
    except Exception as e:
        logger.error(f"Error during token swap: {e}")


if __name__ == "__main__":
    logger.info("Starting Flask server...")
    # Use Heroku's $PORT variable for Flask
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
