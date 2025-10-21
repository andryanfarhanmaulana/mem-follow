# mem-follow: Cross-Chain Bridge Event Listener Simulation

This repository contains a Python script that simulates a critical component of a cross-chain bridge: an event listener, often called a relayer node. This node is responsible for monitoring a bridge contract on a source blockchain (e.g., Ethereum), detecting deposit events, and triggering a corresponding token minting transaction on a destination blockchain (e.g., Polygon).

This script serves as an architectural blueprint, demonstrating key principles for building a reliable off-chain agent: modular design, persistent state management, resilience to network errors, and real-time interaction with blockchain networks.

## Concept

Cross-chain bridges are essential for blockchain interoperability, allowing assets and data to move between otherwise siloed networks. A common design pattern is the "lock-and-mint" mechanism:

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

3.  **Verify & Relay:** Upon detecting a valid event, a relayer node constructs, signs, and broadcasts a new transaction on the destination chain. This transaction calls a function (e.g., `mintBridgedTokens`) on the destination bridge contract.

4.  **Mint/Release:** The destination bridge contract verifies the relayer's message and mints an equivalent amount of "wrapped" tokens to the specified recipient's address.

This script simulates the entire lifecycle of the **Listen** and **Verify & Relay** steps.

## Code Architecture

The script is structured into several distinct classes, each with a single responsibility to promote maintainability and testability.

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
+--------------+----------+  +-----------------+  +----------------------+\n|   ChainConnector        |  |  EventProcessor |  | TransactionBroadcaster |
| (Source & Destination)  |  |-----------------|  |----------------------|
|-------------------------|  | - Validates     |  | - Builds transaction |
| - Manages Web3 connection |  |   event data    |  | - Manages nonce      |
| - Provides contract obj |  | - Prevents      |  | - Signs & Sends      |
| - Retry logic           |  |   replays via   |  | - Waits for receipt  |
+-------------------------+  |   StateDB       |  | - Gas estimation     |
                           +--------+--------+  +----------------------+
                                    |
                               (Checks/Updates)
                                    v
                           +-----------------+\n                           |     StateDB     |
                           |-----------------|
                           | - Persists state|
                           |   (JSON file)   |
                           +-----------------+
```

-   **`ChainConnector`**: Manages the connection to a blockchain's RPC endpoint. It handles initial connection, status checks, provides Web3 contract objects, and includes retry logic for transient RPC failures.
-   **`StateDB`**: A simple persistent state manager that uses a local JSON file. It tracks which event transaction hashes have already been processed to prevent duplicates (replay attacks).
-   **`EventProcessor`**: The business logic core. It takes a raw event, validates its arguments (e.g., checks if it's for the correct destination chain), and transforms it into a structured payload for the destination chain transaction.
-   **`TransactionBroadcaster`**: Responsible for the final step of relaying. It takes processed data, builds the raw transaction (including nonce and gas), signs it with a private key, and broadcasts it to the destination chain. It also includes a `SIMULATE_ONLY` mode.
-   **`BridgeContractListener`**: The main orchestrator. Running in its own thread, it polls for new events in a continuous loop and passes them through the `EventProcessor` and `TransactionBroadcaster`. It also includes logic for handling blockchain reorganizations (reorgs) and connection errors.

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
7.  **Event Processing**: Once an event is confirmed, it is passed to the `EventProcessor`. It checks the `StateDB` to prevent duplicate processing, validates the event's data, and prepares a payload for the minting transaction.
8.  **Transaction Broadcasting**: The prepared data is passed to the `TransactionBroadcaster`. It fetches the current nonce, builds and signs the `mintBridgedTokens` transaction, and sends it to the destination chain (if `SIMULATE_ONLY` is `False`).
9.  **State Update**: Upon successful broadcast, the `StateDB` is updated with the source event's transaction hash, preventing it from being processed again.
10. **Graceful Shutdown**: The script listens for a `KeyboardInterrupt` (Ctrl+C) to shut down the listener thread cleanly.

## Prerequisites

- Python 3.8 or higher.
- Access to RPC endpoints for a source and a destination blockchain (e.g., via [Infura](https://infura.io) or [Alchemy](https://www.alchemy.com/)).

## Usage

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-github-username/mem-follow.git
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
    # The destination chain ID, used to filter events on the source chain
    DEST_CHAIN_ID=80001
    # Address of the destination chain contract that mints bridged tokens
    DEST_MINT_CONTRACT="0xYourDestinationMintContractAddress"

    # --- RELAYER WALLET ---
    # IMPORTANT: Use a dedicated burner wallet funded only with enough native currency (e.g., ETH, MATIC) for gas fees.
    # NEVER USE A WALLET WITH SIGNIFICANT ASSETS for development or production relaying.
    SIGNER_PRIVATE_KEY="0xyour_private_key_here"

    # --- SCRIPT BEHAVIOR ---
    # Set to 'False' to actually broadcast transactions. Default is 'True'.
    SIMULATE_ONLY="True"
    ```

4.  **Run the script:**
    ```bash
    python main.py
    ```

5.  **Observe the output:**
    The script will start logging its status. If a `TokensDeposited` event occurs on the source contract that matches the criteria, you will see logs detailing the processing and (simulated) broadcasting steps.

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