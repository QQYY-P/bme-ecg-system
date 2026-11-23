"""Avoid scheduling Tk redraws for unchanged values (all calls on UI thread)."""
import tkinter as tk
from tkinter import ttk


class ChangedOptions:
    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, dict):
            kwargs = {**cnf, **kwargs}
            cnf = None
        if cnf is not None or not kwargs:
            return super().configure(cnf, **kwargs)
        cache = getattr(self, '_last_options', {})
        changed = {key: value for key, value in kwargs.items() if cache.get(key, object()) != value}
        if changed:
            result = super().configure(**changed)
            cache.update(changed)
            self._last_options = cache
            return result
    config = configure


class Label(ChangedOptions, tk.Label):
    pass


class TtkLabel(ChangedOptions, ttk.Label):
    pass


class TtkButton(ChangedOptions, ttk.Button):
    pass


class Canvas(tk.Canvas):
    def __init__(self, *args, **kwargs):
        self._item_options = {}
        self._item_coords = {}
        super().__init__(*args, **kwargs)

    def coords(self, item, *args):
        if not args or not isinstance(item, int):
            return super().coords(item, *args)
        if self._item_coords.get(item) != args:
            result = super().coords(item, *args)
            self._item_coords[item] = args
            return result

    def itemconfigure(self, item, cnf=None, **kwargs):
        if cnf is not None or not kwargs or not isinstance(item, int):
            self._item_options.clear()
            return super().itemconfigure(item, cnf, **kwargs)
        cache = self._item_options.setdefault(item, {})
        changed = {key: value for key, value in kwargs.items() if cache.get(key, object()) != value}
        if changed:
            result = super().itemconfigure(item, **changed)
            cache.update(changed)
            return result
    itemconfig = itemconfigure

    def delete(self, *items):
        self._item_coords.clear()
        self._item_options.clear()
        return super().delete(*items)
