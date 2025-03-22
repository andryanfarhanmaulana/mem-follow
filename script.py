import os
import time
import json
import logging
from typing import Dict, Any, Optional, List
from threading import Thread, Event

from web3 import Web3
from web3.contract import Contract
from web3.exceptions import TransactionNotFound, BlockNotFound
from web3.middleware import geth_poa_middleware
from requests.exceptions import RequestException
import requests
from dotenv import load_dotenv

# --- Basic Configuration ---
load_dotenv()

# Configure logging to provide detailed output for the simulation
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(threadName)s] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# --- Constants and Mock Data ---
# In a real-world scenario, these ABIs would be loaded from files or a contract registry.
SOURCE_BRIDGE_ABI = json.loads('''
[
    {
        "anonymous": false,
        "inputs": [
            {"indexed": true, "name": "sender", "type": "address"},
            {"indexed": true, "name": "recipient", "type": "address"},
            {"indexed": false, "name": "amount", "type": "uint256"},
            {"indexed": false, "name": "destinationChainId", "type": "uint256"},
            {"indexed": false, "name": "nonce", "type": "uint256"}
        ],
        "name": "TokensDeposited",
        "type": "event"
    }
]
''')

DESTINATION_MINT_ABI = json.loads('''
[
    {
        "constant": false,
        "inputs": [
            {"name": "recipient", "type": "address"},
            {"name": "amount", "type": "uint256"},
            {"name": "sourceTxHash", "type": "bytes32"}
        ],
        "name": "mintBridgedTokens",
        "outputs": [],
        "payable": false,
        "stateMutability": "nonpayable",
        "type": "function"
    }
]
''')

STATE_FILE = 'processed_events_db.json'

class StateDB:
    """
    A simple file-based database to track processed events to prevent replay attacks.
    In a production system, this would be a more robust database like Redis or PostgreSQL.
    """
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.processed_hashes = self._load()

    def _load(self) -> List[str]:
        """Loads the list of processed transaction hashes from the state file."""
        if not os.path.exists(self.filepath):
            return []
        try:
            with open(self.filepath, 'r') as f:
                data = json.load(f)
                return data.get('processed_hashes', [])
        except (IOError, json.JSONDecodeError) as e:
            logging.error(f"Failed to load state DB from {self.filepath}: {e}")
            return []

    def _save(self):
        """Saves the current list of processed hashes to the state file."""
        try:
            with open(self.filepath, 'w') as f:
                json.dump({'processed_hashes': self.processed_hashes}, f, indent=4)
        except IOError as e:
            logging.error(f"Failed to save state DB to {self.filepath}: {e}")

    def is_processed(self, tx_hash: str) -> bool:
        """Checks if a given transaction hash has already been processed."""
        return tx_hash in self.processed_hashes

    def mark_as_processed(self, tx_hash: str):
        """Adds a transaction hash to the list of processed events and saves the state."""
        if not self.is_processed(tx_hash):
            self.processed_hashes.append(tx_hash)
            self._save()
            logging.info(f"Marked transaction {tx_hash} as processed.")

class ChainConnector:
    """
    Manages the connection to a blockchain node via a Web3 provider.
    Includes basic retry logic for establishing a connection.
    """
    def __init__(self, chain_name: str, rpc_url: str):
        self.chain_name = chain_name
        self.rpc_url = rpc_url
        self.web3: Optional[Web3] = None
        self.connect()

    def connect(self, max_retries: int = 3, delay: int = 5):
        """Attempts to connect to the blockchain RPC endpoint with retries."""
        for attempt in range(max_retries):
            try:
                self.web3 = Web3(Web3.HTTPProvider(self.rpc_url))
                # Middleware for PoA chains like Goerli, Sepolia, Polygon
                self.web3.middleware_onion.inject(geth_poa_middleware, layer=0)
                if self.web3.is_connected():
                    logging.info(f"Successfully connected to {self.chain_name} at {self.rpc_url}")
                    return
                else:
                    raise ConnectionError("Web3 provider is not connected.")
            except Exception as e:
                logging.warning(
                    f"Connection attempt {attempt + 1}/{max_retries} to {self.chain_name} failed: {e}. Retrying in {delay}s..."
                )
                time.sleep(delay)
        logging.error(f"Could not connect to {self.chain_name} after {max_retries} attempts.")
        raise ConnectionError(f"Failed to connect to {self.chain_name}.")

    def get_contract(self, address: str, abi: List[Dict]) -> Optional[Contract]:
        """Returns a Web3 contract instance if connected."""
        if not self.web3 or not self.web3.is_connected():
            logging.error(f"Not connected to {self.chain_name}, cannot get contract.")
            return None
        return self.web3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)

class EventProcessor:
    """
    Processes raw event data, validates it, and prepares the transaction for the destination chain.
    """
    def __init__(self, state_db: StateDB, destination_chain_id: int):
        self.state_db = state_db
        self.destination_chain_id = destination_chain_id

    def process_event(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Validates and transforms a source chain event into a destination chain transaction.
        Returns a dictionary representing the transaction to be broadcast, or None if invalid.
        """
        tx_hash = event['transactionHash'].hex()
        if self.state_db.is_processed(tx_hash):
            logging.warning(f"Event for tx {tx_hash} already processed. Skipping.")
            return None

        event_args = event['args']
        logging.info(f"Processing new event from tx: {tx_hash}")

        # --- Validation Logic ---
        # 1. Check if the event is intended for our destination chain
        if event_args.get('destinationChainId') != self.destination_chain_id:
            logging.info(f"Event for tx {tx_hash} is for a different chain. Skipping.")
            return None

        # 2. Extract and validate required arguments
        recipient = event_args.get('recipient')
        amount = event_args.get('amount')
        if not all([recipient, amount]):
            logging.error(f"Event for tx {tx_hash} is missing required arguments. Skipping.")
            return None

        # --- Transaction Preparation ---
        # Prepare the raw transaction data for the destination chain
        prepared_tx = {
            'recipient': recipient,
            'amount': amount,
            'source_tx_hash': event['transactionHash'],
            'original_event': event # For logging and debugging
        }

        logging.info(f"Prepared transaction for destination chain: {prepared_tx}")
        return prepared_tx

class TransactionBroadcaster:
    """
    Simulates signing and broadcasting transactions on the destination chain.
    Handles nonce management, gas estimation, and transaction receipt confirmation.
    """
    def __init__(self, connector: ChainConnector, contract: Contract, private_key: str, simulate_only: bool = True):
        if not connector.web3:
            raise ValueError("Connector must be initialized and connected.")
        self.web3 = connector.web3
        self.contract = contract
        self.private_key = private_key
        self.account = self.web3.eth.account.from_key(private_key)
        self.simulate_only = simulate_only

    def get_gas_price_from_api(self, api_url: str) -> Optional[int]:
        """Fetches the recommended gas price from an external oracle API."""
        try:
            response = requests.get(api_url)
            response.raise_for_status()
            data = response.json()
            # Example for Polygon Gas Station API
            gas_price_gwei = data.get('fast', {}).get('maxFee')
            if gas_price_gwei:
                return self.web3.to_wei(gas_price_gwei, 'gwei')
            return None
        except RequestException as e:
            logging.error(f"Failed to fetch gas price from API: {e}")
            return None

    def broadcast(self, tx_data: Dict[str, Any]) -> Optional[str]:
        """
        Builds, signs, and broadcasts a transaction.
        Returns the transaction hash on success, None on failure.
        """
        try:
            nonce = self.web3.eth.get_transaction_count(self.account.address)

            # Build the transaction for the 'mintBridgedTokens' function call
            tx_payload = self.contract.functions.mintBridgedTokens(
                tx_data['recipient'],
                tx_data['amount'],
                tx_data['source_tx_hash']
            ).build_transaction({
                'from': self.account.address,
                'nonce': nonce,
                'gas': 200000, # A safe gas limit; use estimateGas in production
                'gasPrice': self.web3.eth.gas_price # Use API for better EIP-1559 support
            })

            signed_tx = self.web3.eth.account.sign_transaction(tx_payload, self.private_key)
            
            logging.info(f"Broadcasting tx for source hash {tx_data['source_tx_hash'].hex()}...")

            if self.simulate_only:
                logging.warning(f"[SIMULATION MODE] Would broadcast transaction. Hash: {signed_tx.hash.hex()}")
                # In simulation mode, we return a mock hash
                return signed_tx.hash.hex()
            else:
                tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
                logging.info(f"Transaction broadcasted! Hash: {tx_hash.hex()}")
                
                # Wait for receipt for confirmation
                receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
                if receipt.status == 1:
                    logging.info(f"Transaction confirmed successfully in block {receipt.blockNumber}.")
                    return tx_hash.hex()
                else:
                    logging.error(f"Transaction failed! Receipt: {receipt}")
                    return None

        except Exception as e:
            logging.error(f"Failed to broadcast transaction: {e}")
            return None

class BridgeContractListener(Thread):
    """
    The main orchestrator. Listens for events on the source chain bridge contract
    and coordinates the processing and broadcasting workflow.
    """
    def __init__(self, source_connector: ChainConnector, dest_broadcaster: TransactionBroadcaster, 
                 event_processor: EventProcessor, source_contract_address: str, 
                 poll_interval: int = 10, confirmation_blocks: int = 6):
        super().__init__()
        self.name = "BridgeListenerThread"
        self.source_connector = source_connector
        self.dest_broadcaster = dest_broadcaster
        self.event_processor = event_processor
        self.poll_interval = poll_interval
        self.confirmation_blocks = confirmation_blocks
        self.stop_event = Event()

        if not self.source_connector.web3:
            raise ValueError("Source connector is not connected.")

        self.source_contract = self.source_connector.get_contract(source_contract_address, SOURCE_BRIDGE_ABI)
        if not self.source_contract:
            raise ConnectionError("Failed to initialize source contract.")
        
        self.event_filter = self.source_contract.events.TokensDeposited.create_filter(fromBlock='latest')

    def stop(self):
        """Signals the thread to stop its execution loop gracefully."""
        self.stop_event.set()
        logging.info("Stop signal received. Shutting down listener...")

    def run(self):
        """The main event listening loop."""
        logging.info(f"Starting to listen for 'TokensDeposited' events on {self.source_contract.address}...")
        while not self.stop_event.is_set():
            try:
                latest_block = self.source_connector.web3.eth.block_number
                events = self.event_filter.get_new_entries()

                for event in events:
                    # Basic re-org protection: wait for a few blocks to confirm the event
                    event_block = event.get('blockNumber')
                    if event_block and (latest_block - event_block) < self.confirmation_blocks:
                        logging.info(f"Event in block {event_block} is too recent. Waiting for confirmations...")
                        continue

                    prepared_tx = self.event_processor.process_event(event)
                    if prepared_tx:
                        broadcast_hash = self.dest_broadcaster.broadcast(prepared_tx)
                        if broadcast_hash:
                            # Mark original source transaction as processed
                            source_tx_hash = prepared_tx['source_tx_hash'].hex()
                            self.event_processor.state_db.mark_as_processed(source_tx_hash)
                
                time.sleep(self.poll_interval)

            except BlockNotFound:
                logging.warning("Block not found, possibly due to a re-org. Re-initializing filter.")
                self.event_filter = self.source_contract.events.TokensDeposited.create_filter(fromBlock='latest')
                time.sleep(self.poll_interval * 2)
            except Exception as e:
                logging.error(f"An error occurred in the listener loop: {e}", exc_info=True)
                time.sleep(self.poll_interval * 2) # Longer sleep on error

def main():
    """Main function to set up and run the bridge listener simulation."""
    # --- Configuration from .env file ---
    SOURCE_CHAIN_RPC = os.getenv('SOURCE_CHAIN_RPC')
    SOURCE_BRIDGE_CONTRACT = os.getenv('SOURCE_BRIDGE_CONTRACT')
    DEST_CHAIN_RPC = os.getenv('DEST_CHAIN_RPC')
    DEST_MINT_CONTRACT = os.getenv('DEST_MINT_CONTRACT')
    DEST_CHAIN_ID = int(os.getenv('DEST_CHAIN_ID', 80001)) # Default to Mumbai testnet
    SIGNER_PRIVATE_KEY = os.getenv('SIGNER_PRIVATE_KEY')
    SIMULATE_ONLY = os.getenv('SIMULATE_ONLY', 'True').lower() in ('true', '1', 't')

    if not all([SOURCE_CHAIN_RPC, SOURCE_BRIDGE_CONTRACT, DEST_CHAIN_RPC, DEST_MINT_CONTRACT, SIGNER_PRIVATE_KEY]):
        logging.error("One or more environment variables are not set. Please check your .env file.")
        return

    try:
        # 1. Initialize State DB
        state_db = StateDB(STATE_FILE)

        # 2. Setup Chain Connectors
        source_connector = ChainConnector("SourceChain", SOURCE_CHAIN_RPC)
        dest_connector = ChainConnector("DestinationChain", DEST_CHAIN_RPC)

        # 3. Setup core components
        event_processor = EventProcessor(state_db, DEST_CHAIN_ID)
        
        dest_contract = dest_connector.get_contract(DEST_MINT_CONTRACT, DESTINATION_MINT_ABI)
        if not dest_contract:
            return
        
        tx_broadcaster = TransactionBroadcaster(
            connector=dest_connector,
            contract=dest_contract,
            private_key=SIGNER_PRIVATE_KEY,
            simulate_only=SIMULATE_ONLY
        )

        # 4. Initialize and start the listener thread
        listener = BridgeContractListener(
            source_connector=source_connector,
            dest_broadcaster=tx_broadcaster,
            event_processor=event_processor,
            source_contract_address=SOURCE_BRIDGE_CONTRACT
        )
        
        listener.start()

        # Keep the main thread alive to handle graceful shutdown
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logging.info("Keyboard interrupt received.")
            listener.stop()
            listener.join() # Wait for the thread to finish

    except (ConnectionError, ValueError) as e:
        logging.error(f"Initialization failed: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred during setup: {e}", exc_info=True)

if __name__ == "__main__":
    main()
