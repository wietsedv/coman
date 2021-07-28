import click


class NaturalOrderGroup(click.Group):
    def list_commands(self, _):
        return self.commands.keys()
