## Developing with Visual Studio Code + devcontainer

The easiest way to get started with custom integration development is to use Visual Studio Code with devcontainers. This approach will create a preconfigured development environment with all the tools you need.

In the container you will have a dedicated Home Assistant core instance running with your custom component code. You can configure this instance by updating [`.devcontainer/configuration.yaml`](./configuration.yaml); it is copied to `config/configuration.yaml` each time you start the development task.

**Prerequisites**

- [git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git)
- Docker
  - For Linux, macOS, or Windows 10 Pro/Enterprise/Education use the [current release version of Docker](https://docs.docker.com/install/)
  - Windows 10 Home requires [WSL 2](https://docs.microsoft.com/windows/wsl/wsl2-install) and the current Edge version of Docker Desktop (see instructions [here](https://docs.docker.com/docker-for-windows/wsl-tech-preview/)). This can also be used for Windows Pro/Enterprise/Education.
- [Visual Studio Code](https://code.visualstudio.com/)
- [Dev Containers (VS Code Extension)][extension-link]

[More info about requirements and devcontainer in general](https://code.visualstudio.com/docs/remote/containers#_getting-started)

[extension-link]: https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers

**Getting started:**

1. Fork the repository.
2. Clone the repository to your computer.
3. Open the repository using Visual Studio Code.

When you open this repository with Visual Studio Code you are asked to "Reopen in Container", this will start the build of the container.

_If you don't see this notification, open the command palette and select `Dev Containers: Reopen Folder in Container`._

### Tasks

The devcontainer comes with some useful tasks to help you with development. Start them from the command palette with `Tasks: Run Task`.

When a task is currently running (like `Run Home Assistant on port 8123`), it can be restarted with `Tasks: Restart Running Task`.

The available tasks are:

Task | Description
-- | --
Run Home Assistant on port 8123 | Launch Home Assistant with your custom component code and the configuration from `.devcontainer/configuration.yaml`.
Run Home Assistant configuration check | Validate the Home Assistant configuration in `config/`.
Upgrade Home Assistant to latest | Upgrade the Home Assistant version installed in the container to the latest PyPI release.
Install a specific version of Home Assistant | Install a specific Home Assistant version from PyPI (you will be prompted for the version).
Lint | Format and lint the codebase with Ruff.
Run tests | Run the pytest suite from `tests/`.

### Step by step debugging

With the development container you can test your custom component in Home Assistant with step-by-step debugging.

1. Keep `debugpy:` enabled in [`.devcontainer/configuration.yaml`](./configuration.yaml) (`start: true`, `wait: true`).
2. Launch the task `Run Home Assistant on port 8123` (or use the debug config `Python: Run debug`, which starts that task for you).
3. Start / attach the debugger with `Python: Attach Local` (port 5678).
4. Open http://localhost:8123 and add the **Lidl Plus** integration.

For more information, see [the debugpy integration documentation](https://www.home-assistant.io/integrations/debugpy/).
