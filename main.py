"""
Entry point. Run this file:
 
    python main.py
 
See README.md in this folder for setup instructions and how the
Model / View / Controller pieces fit together.
"""
 
import tkinter as tk
from controller import TranslatorController
 
if __name__ == "__main__":
    root = tk.Tk()
    app = TranslatorController(root)
    root.mainloop()
 