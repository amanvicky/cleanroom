"""Notes GUI — cleanroom's tkinter demo fixture.

On the live runner this renders on the app screen (the runner installs a
virtual tkinter — a subset rendered as real HTML widgets). On a real PC it
opens a normal tkinter window.
"""

import tkinter as tk
from tkinter import messagebox


def main():
    root = tk.Tk()
    root.title("Notes")

    tk.Label(root, text="My notes", font=("Helvetica", 16, "bold")).pack(pady=6)

    row = tk.Frame(root)
    row.pack(fill="x", padx=10)
    note = tk.Entry(row, width=32)
    note.pack(side="left", fill="x", expand=True)

    def add():
        text = note.get().strip()
        if not text:
            messagebox.showinfo("Notes", "type a note first")
            return
        notes.insert("end", text)
        note.delete(0, "end")
        count.set("%d notes" % notes.size())

    tk.Button(row, text="add", command=add).pack(side="left", padx=6)

    notes = tk.Listbox(root, height=6)
    notes.pack(fill="both", expand=True, padx=10, pady=8)

    count = tk.StringVar(value="0 notes")
    tk.Label(root, textvariable=count).pack(pady=4)

    tk.Button(root, text="quit", command=root.destroy).pack(pady=6)

    root.mainloop()


if __name__ == "__main__":
    main()
