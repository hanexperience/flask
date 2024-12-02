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

# Web3 Configuration with Alchemy
ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY")
if not ALCHEMY_API_KEY:
    raise ValueError("Alchemy API key not found. Please set ALCHEMY_API_KEY in your .env file.")

RPC_URL = f"https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
web3 = Web3(Web3.HTTPProvider(RPC_URL))

# Uniswap V3 Router Address and ABI
router_address = web3.to_checksum_address("0x2626664c2603336E57B271c5C0b26F421741e481")  # Uniswap V3 Router
token_out = web3.to_checksum_address("0x4200000000000000000000000000000000000006")  # USDC on Ethereum

# Load ABIs
script_dir = os.path.dirname(os.path.abspath(__file__))  # Get the script's directory
with open(os.path.join(script_dir, "SwapRouterABI.json")) as f:
    router_abi = json.load(f)
with open(os.path.join(script_dir, "ERC20ABI.json")) as f:
    erc20_abi = json.load(f)

# Event ABI for TokenDeployed(address indexed tokenAddress)
token_deployed_event_abi = {
    "anonymous": False,
    "inputs": [
        {
            "indexed": True,
            "internalType": "address",
            "name": "tokenAddress",
            "type": "address"
        }
    ],
    "name": "TokenDeployed",
    "type": "event"
}


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
# Follower threshold and swap amount
FOLLOWER_THRESHOLD = int(os.getenv("FOLLOWER_THRESHOLD", 1000))  # Default to 1000
DEFAULT_SWAP_AMOUNT = web3.to_wei(float(os.getenv("SWAP_AMOUNT", 0.0001)), 'ether') 


app = Flask(__name__)


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
        


def format_follower_count(follower_count):
    """Formats the follower count with commas for better readability."""
    try:
        count = int(follower_count)  # Convert to an integer
        return "{:,}".format(count)  # Add commas
    except (ValueError, TypeError):
        return "N/A"  # Return "N/A" if invalid


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
MONITORED_FID = "874542"  # Replace with the desired fid


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


processed_events = set()  # Global set to track processed events

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.json
        logger.debug("Received webhook request.")

        # Extract relevant data
        cast_data = data.get("data", {})
        cast_hash = cast_data.get("hash")
        if not cast_hash:
            logger.error("No cast hash in webhook data.")
            return jsonify({"status": "Invalid data"}), 400
        
        # Deduplicate
        if cast_hash in processed_events:
            logger.info(f"Duplicate event detected: {cast_hash}. Skipping.")
            return jsonify({"status": "Duplicate event"}), 200
        processed_events.add(cast_hash)

        # Extract and validate necessary data
        author_fid = str(cast_data.get("author", {}).get("fid", ""))
        original_message_text = cast_data.get("text", "")
        logger.debug(f"Author FID: {author_fid}")
        logger.debug(f"Message Text: {original_message_text}")

        # Ensure the message contains the required keyword
        if "your token" not in original_message_text.lower():
            logger.info("Original message does not contain the required keyword. Ignored.")
            return jsonify({"status": "No matching message"}), 200
        
        # Extract and validate the clanker address
        clanker_url = extract_clanker_url(original_message_text)
        clanker_address = extract_clanker_address(clanker_url)
        if not clanker_address:
            logger.error("No valid contract address found in the message.")
            return jsonify({"status": "No valid contract address"}), 400

        logger.info(f"Valid clanker address: {clanker_address}. Initiating swap...")

        # Initiate the token swap
        swap_response = swap_tokens_v3(
            wallet=wallet,
            router_contract=router_contract,
            token_in=weth_address,
            token_out=clanker_address,
            fee_tier=fee_tier,
            amount_in=DEFAULT_SWAP_AMOUNT
        )

        if swap_response["status"] == "success":
            logger.info(f"Swap successful! Tx Hash: {swap_response['tx_hash']}")
            return jsonify({"status": "Swap successful", "tx_hash": swap_response["tx_hash"]}), 200
        else:
            logger.error(f"Swap failed: {swap_response['message']}")
            return jsonify({"status": "Swap failed", "error": swap_response["message"]}), 500

    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return jsonify({"status": "Error processing webhook"}), 500



# Updated swap function with additional safety checks
def swap_tokens_v3(wallet, router_contract, token_in, token_out, fee_tier, amount_in):
    """
    Performs a token swap using Uniswap V3.
    Args:
        wallet: Tuple of (address, private_key).
        router_contract: The router contract instance.
        token_in: Address of the input token.
        token_out: Address of the output token.
        fee_tier: Uniswap V3 fee tier.
        amount_in: Amount of input tokens to swap.
    Returns:
        dict: Transaction hash or error message.
    """
    address, private_key = wallet
    try:
        logger.info(f"Starting token swap: {amount_in} of {token_in} to {token_out} with fee tier {fee_tier}.")
        
        # Approve router to spend token_in
        token_in_contract = web3.eth.contract(address=token_in, abi=erc20_abi)
        allowance = token_in_contract.functions.allowance(address, router_address).call()
        if allowance < amount_in:
            logger.debug(f"Allowance for {token_in} is {allowance}, approving {amount_in}...")
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
        logger.info("Building and sending the swap transaction...")
        params = {
            "tokenIn": token_in,
            "tokenOut": token_out,
            "fee": fee_tier,
            "recipient": address,
            "deadline": int(time.time()) + 300,
            "amountIn": amount_in,
            "amountOutMinimum": 1,  # Accept any positive amount
            "sqrtPriceLimitX96": 0  # No price limit
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
        return {"status": "success", "tx_hash": tx_hash.hex()}

    except Exception as e:
        logger.error(f"Error during token swap: {str(e)}")
        return {"status": "error", "message": str(e)}



if __name__ == "__main__":
    logger.info("Starting Flask server...")

    # Check if DEBUG_MODE is enabled
    debug_mode = os.getenv("DEBUG_MODE", "false").lower() == "true"
    if debug_mode:
        logger.info("DEBUG_MODE is enabled. Performing a test swap...")

        # Define test parameters
        test_token_out = web3.to_checksum_address("0x2E9b00C64Cb36BC2d51352d818bDF2E682B3B94C")  # Replace with a sample token address
        test_amount = web3.to_wei(0.0001, "ether")  # Replace with your test amount

        # Perform the swap
        swap_response = swap_tokens_v3(
            wallet=wallet,
            router_contract=router_contract,
            token_in=weth_address,
            token_out=test_token_out,
            fee_tier=fee_tier,
            amount_in=test_amount,
        )

        if swap_response.get("status") == "success":
            logger.info(f"Test swap successful! Tx Hash: {swap_response['tx_hash']}")
        else:
            logger.error(f"Test swap failed: {swap_response.get('message')}")

    # Start Flask server
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
