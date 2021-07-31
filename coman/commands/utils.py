import click


# class NaturalOrderGroup(click.Group):
#     def list_commands(self, _):
#         return self.commands.keys()
# @click.group(cls=NaturalOrderGroup)


def add_options(fn, options):
    for option in reversed(options):
        fn = option(fn)
    return fn
