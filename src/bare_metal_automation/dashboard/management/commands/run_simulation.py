"""Management command to run a simulated deployment from the CLI."""

from django.core.management.base import BaseCommand

from bare_metal_automation.dashboard.simulation import SimulationEngine


class Command(BaseCommand):
    help = "Run a simulated deployment through all phases (no hardware required)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--name",
            default="SIM-Rack-Demo",
            help="Name for the simulated deployment (default: SIM-Rack-Demo)",
        )

    def handle(self, *args, **options):
        name = options["name"]
        self.stdout.write(f"Starting simulation: {name}")
        self.stdout.write("Press Ctrl+C to stop.\n")

        engine = SimulationEngine(deployment_name=name)
        try:
            engine.run()
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("\nSimulation interrupted."))
            return

        if engine.deployment and engine.deployment.phase == "complete":
            self.stdout.write(self.style.SUCCESS(
                f"Simulation '{name}' completed successfully."
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f"Simulation '{name}' ended in phase: "
                f"{engine.deployment.phase if engine.deployment else 'unknown'}"
            ))
