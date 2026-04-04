# Convenience commands for local development and testing of the agent
PHONY: init-backend init-frontend init clean bench run run-fe run-all restart restart-all stop stop-all

init-backend:
	@echo "Initializing backend..."
	cd backend && ./setup.sh

init-frontend:
	@echo "Initializing frontend..."
	cd frontend && npm install

init: init-backend init-frontend
	@echo "Initialization complete!"

clean:
	@echo "Cleaning up..."
	@rm -rf ./backend/venv
	@rm -rf ./src/node_modules
	@rm -rf ./src/dist
	@echo "Cleanup complete!"

bench:
	@echo "Running benchmarks..."
	@echo "**THIS COULD TAKE A WHILE**"
	@./scripts/run-bench.sh

run:
	@echo "Running agent without UI..."
	@docker compose -f docker-compose.yml up -d --build

run-fe:
	@echo "Running frontend only..."
	@cd src && npm run dev

run-all:
	@echo "Running complete agent service (UI, Backend, MCP Servers)..."
	@docker compose -f docker-compose-full.yml up -d --build

restart: 
	@echo "Restarting Agent..."
	@make stop
	@make run

restart-all:
	@echo "Restarting all services..."
	@make stop-all
	@make run-all

stop:
	@echo "Stopping Agent..."
	@docker compose -f docker-compose.yml down

stop-all:
	@echo "Stopping agent services..."
	@docker compose -f docker-compose-full.yml down
