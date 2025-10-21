# mem-follow: Cross-Chain Bridge Event Listener Simulation

This repository contains a Python script that simulates a critical component of a cross-chain bridge: an event listener, often called a relayer node. This node is responsible for monitoring a bridge contract on a source blockchain (e.g., Ethereum), detecting specific events (e.g., token deposits), and triggering a corresponding transaction on a destination blockchain (e.g., Polygon).

This script serves as an architectural blueprint, demonstrating key principles for building a reliable off-chain agent: modular design, persistent state management, resilience to network errors, and robust interaction with blockchain networks.

## Concept

Cross-chain bridges are essential for blockchain interoperability, allowing assets and data to move between otherwise siloed networks. A common design pattern for this is the "lock-and-mint" mechanism:

1.  **Lock/Deposit:** A user deposits tokens (e.g., ERC-20) into a bridge contract on the source chain. The contract locks these tokens and emits an event (`TokensDeposited`) containing details like the recipient's address on the destination chain, the amount, and the destination chain ID.

    ```solidity
    // Example event in the source bridge contract
    event TokensDeposited(
        address indexed user,
        address indexed recipient,
        uint256 amount,
        uint256 destinationChainId
    );
    ```

2.  **Listen:** Off-chain nodes, often called validators or relayers, continuously monitor the source chain for these `TokensDeposited` events.

3.  **Verify & Relay:** Upon detecting a valid event, the relayer node constructs, signs, and broadcasts a new transaction on the destination chain. This transaction calls a function (e.g., `mintBridgedTokens`) on the destination bridge contract.

4.  **Mint/Release:** The destination bridge contract verifies the relayer's message and mints an equivalent amount of "wrapped" tokens to the specified recipient's address.

This script simulates the entire lifecycle of the **Listen** and **Verify & Relay** steps.

## Features

-   **Modular & Extensible:** Components are decoupled, making it easy to replace the state manager, add new event processors, or change the transaction broadcasting logic.
-   **Persistent State:** Remembers processed events in a JSON file to prevent double-processing across restarts.
-   **Resilience:** Includes retry logic for RPC connections and handles graceful shutdown.
-   **Reorg Protection:** Waits for a configurable number of block confirmations before processing an event, reducing the risk of acting on orphaned blocks.
-   **Simulation Mode:** A `SIMULATE_ONLY` flag allows for testing the entire listening and processing pipeline without broadcasting live transactions, saving gas fees during development.
-   **Configuration-Driven:** All critical parameters (RPC URLs, keys, contract addresses) are managed via a `.env` file and are not hardcoded.

## Code Architecture

The script is structured into several distinct classes, each with a single responsibility, which promotes maintainability and testability.

```
+---------------------------+
|          main.py          |
| (Setup & Orchestration)   |
+-------------+-------------+
              |
              | Starts
              v
+-------------+-----------------+
|   BridgeContractListener      |  (Thread)
|-------------------------------|
| - Manages event filter        |
| - Main listening loop         |
| - Handles reorgs, errors      |
| - Coordinates other components|
+-------------+-----------------+
              |                   |                      |
      (1. Event) v                  (2. Process) v             (3. Broadcast) v
+--------------+----------+  +-----------------+  +----------------------+
|   ChainConnector        |  |  EventProcessor |  | TransactionBroadcaster |
| (Source & Destination)  |  |-----------------|  |----------------------|
|-------------------------|  | - Validates     |  | - Builds transaction |
| - Manages Web3 connection |  |   event data    |  | - Manages nonce      |
| - Instantiates contract |  | - Prevents      |  | - Signs & Sends      |
| - Retry logic           |  |   replays via   |  | - Waits for receipt  |
+-------------------------+  |   StateDB       |  | - Gas estimation     |
                           +--------+--------+  +----------------------+
                                    |
                               (Checks/Updates)
                                    v
                           +-----------------+
                           |     StateDB     |
                           |-----------------|
                           | - Persists state|
                           |   (JSON file)   |
                           +-----------------+
```

-   **`ChainConnector`**: Manages the connection to a blockchain's RPC endpoint. It handles the initial connection, status checks, instantiates `web3.py` contract objects, and includes retry logic for transient RPC failures.
-   **`StateDB`**: A simple persistent state manager that uses a local JSON file. It tracks which event transaction hashes have already been processed to prevent duplicates (replay attacks).
-   **`EventProcessor`**: The business logic core. It takes a raw event, validates its arguments (e.g., checks if it's for the correct destination chain), and transforms it into a structured payload for the destination chain transaction.
-   **`TransactionBroadcaster`**: Responsible for the final step of relaying. It takes processed data, builds the raw transaction (including nonce and gas), signs it with a private key, and broadcasts it to the destination chain. It also includes a `SIMULATE_ONLY` mode.
-   **`BridgeContractListener`**: The main orchestrator. Running in its own thread, it polls for new events in a continuous loop, delegating them to the `EventProcessor` and `TransactionBroadcaster`. It also includes logic for handling blockchain reorganizations (reorgs) and connection errors.

**Component Orchestration**

The components are instantiated and wired together in `main.py`, providing a clear separation of concerns:

```python
# Simplified example of component setup in main.py

# 1. Setup individual components
source_chain = ChainConnector(config.SOURCE_CHAIN_RPC)
dest_chain = ChainConnector(config.DEST_CHAIN_RPC)
state_db = StateDB("processed_events_db.json")
broadcaster = TransactionBroadcaster(dest_chain, config.SIGNER_PRIVATE_KEY)

# 2. Instantiate the main listener with its dependencies
listener = BridgeContractListener(
    source_chain=source_chain,
    contract_address=config.SOURCE_BRIDGE_CONTRACT,
    state_db=state_db,
    broadcaster=broadcaster,
    dest_chain_id=config.DEST_CHAIN_ID
)

# 3. Start the listener thread
listener.start()
```

## How It Works

1.  **Initialization**: The `main` function reads configuration from a `.env` file, including RPC URLs, contract addresses, and a private key for the relayer wallet.
2.  **Connection**: It establishes connections to both the source and destination chains using `ChainConnector` instances.
3.  **State Loading**: `StateDB` loads the list of previously processed transaction hashes from `processed_events_db.json`.
4.  **Listener Start**: The `BridgeContractListener` is instantiated and started in a separate thread.
5.  **Polling Loop**: The listener thread creates an event filter and enters a loop, periodically polling for new `TokensDeposited` events.
6.  **Confirmation Delay**: To protect against blockchain reorganizations (reorgs), the script waits for a configurable number of blocks (`CONFIRMATION_BLOCKS`) to pass before processing an event.
7.  **Event Processing**: Once an event is confirmed, it is delegated to the `EventProcessor`. This component first checks the `StateDB` to ensure the event has not been processed before (to prevent replays). It then validates the event's data and prepares a structured payload for the minting transaction.
8.  **Transaction Broadcasting**: The prepared data is passed to the `TransactionBroadcaster`. It fetches the current nonce, builds and signs the `mintBridgedTokens` transaction, and sends it to the destination chain (if `SIMULATE_ONLY` is `False`).
9.  **State Update**: Upon successful broadcast, the `StateDB` is updated with the source event's transaction hash, preventing it from being processed again.
10. **Graceful Shutdown**: The script listens for a `KeyboardInterrupt` (Ctrl+C) to shut down the listener thread cleanly.

## Prerequisites

-   Python 3.8 or higher
-   Access to RPC endpoints for a source and a destination blockchain (e.g., via [Infura](https://infura.io) or [Alchemy](https://www.alchemy.com/))

## Usage

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/example-user/mem-follow.git
    cd mem-follow
    ```

2.  **Create a virtual environment and install dependencies:**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
    pip install --upgrade pip
    pip install -r requirements.txt
    ```

3.  **Set up your environment variables:**
    Create a `.env` file from the example. This file is where you'll store your secret keys and RPC endpoints, keeping them out of version control.
    ```bash
    cp .env.example .env
    ```
    Now, populate `.env` with your own data. You can export a private key from a new, dedicated account in a wallet like MetaMask.

    **Example `.env` file:**
    ```env
    # --- SOURCE CHAIN (e.g., Ethereum Sepolia Testnet) ---
    SOURCE_CHAIN_RPC="https://sepolia.infura.io/v3/YOUR_INFURA_PROJECT_ID"
    # Address of the source chain bridge contract to listen to
    SOURCE_BRIDGE_CONTRACT="0xYourSourceBridgeContractAddress"

    # --- DESTINATION CHAIN (e.g., Polygon Mumbai Testnet) ---
    DEST_CHAIN_RPC="https://polygon-mumbai.infura.io/v3/YOUR_INFURA_PROJECT_ID"
    # The destination chain ID; the listener filters for events matching this ID
    DEST_CHAIN_ID=80001
    # Address of the destination chain contract that mints bridged tokens
    DEST_MINT_CONTRACT="0xYourDestinationMintContractAddress"

    # --- RELAYER WALLET ---
    # IMPORTANT: Use a dedicated "burner" wallet funded only with enough native currency (e.g., SepoliaETH, Mumbai MATIC) for gas fees.
    # **NEVER use a wallet containing significant assets.**
    SIGNER_PRIVATE_KEY="0xyour_private_key_here"

    # --- SCRIPT BEHAVIOR ---
    # Set to 'False' to actually broadcast transactions. Default is 'True'.
    SIMULATE_ONLY="True"
    ```

4.  **Run the script:**
    ```bash
    python main.py
    ```
    The listener will now start polling for events.

5.  **Trigger a Test Event (Optional)**
    To see the listener in action, you'll need to trigger a `TokensDeposited` event on the source contract. The easiest way is to interact with the contract using a block explorer like Etherscan (via the "Write Contract" tab) or a wallet interface.

    For developers, a simple script using a library like `web3.py` can also be used. Below is a minimal example of how you might call a `depositTokens` function:
    ```python
    # NOTE: This is a separate, one-off script you would run to test the listener.
    # It requires its own setup with web3, a contract ABI, and a signer.

    from web3 import Web3

    # Assume 'bridge_contract' is an initialized web3.py contract object,
    # 'web3' is your Web3 instance, and 'account' is an initialized
    # account object (from a private key).

    tx_params = {
        'from': account.address,
        'nonce': web3.eth.get_transaction_count(account.address),
        'gasPrice': web3.eth.gas_price,
    }

    tx = bridge_contract.functions.depositTokens(
        '0xRecipientAddressOnDestinationChain', # Recipient's address
        web3.to_wei(10, 'ether'),               # Amount of tokens to bridge
        80001                                  # Destination chain ID (e.g., 80001 for Mumbai)
    ).build_transaction(tx_params)

    signed_tx = web3.eth.account.sign_transaction(tx, private_key=account.key)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    print(f"Sent deposit transaction: {tx_hash.hex()}")
    ```

6.  **Observe the output:**
    Once your deposit transaction is confirmed on the source chain (and after the script's `CONFIRMATION_BLOCKS` delay), you will see logs detailing the processing and (simulated) broadcasting steps.

    ```
    2023-10-27 15:30:00 - INFO - [MainThread] - Successfully connected to SourceChain at https://sepolia.infura.io/v3/...
    2023-10-27 15:30:01 - INFO - [MainThread] - Successfully connected to DestinationChain at https://polygon-mumbai.infura.io/v3/...
    2023-10-27 15:30:01 - INFO - [BridgeListenerThread] - Starting to listen for 'TokensDeposited' events on 0xYourSourceBridgeContractAddress...
    ...
    2023-10-27 15:32:15 - INFO - [BridgeListenerThread] - Processing new event from tx: 0x123abc...def456
    2023-10-27 15:32:15 - INFO - [BridgeListenerThread] - Prepared transaction for destination chain: {'recipient': '0x...', 'amount': 100000000, ...}
    2023-10-27 15:32:15 - INFO - [BridgeListenerThread] - Broadcasting tx for source hash 0x123abc...def456...
    2023-10-27 15:32:16 - WARNING - [BridgeListenerThread] - [SIMULATION MODE] Would broadcast transaction. Hash: 0x789ghi...jkl123
    2023-10-27 15:32:16 - INFO - [BridgeListenerThread] - Marked transaction 0x123abc...def456 as processed.
    ```