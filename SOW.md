# CS 457 Project Statement of Work (SOW) \& Protocol Specification Template

**Student Name:** Sean Blythe 
**Date:** 2026-09-20  
**Course:** CS 457 - Computer Networks  
**Target Server Domain:** `server.blythe.edu`

\---

## 1\. Game Selection \& Scope (Sprint 0)

> Planning is going to be an iterative process through the sprints so you don't have to have all the details now. Focus on big overview concepts. You will be updating the SOW as we plan.
> You have a lot of freedom to choose a game. There are a couple caveats.  

> - It must run in the console. The lab nodes won't be able to handle extensive graphics.
> - It has to be self-contained. You can use a internet-connector to download you code, but because the architecture must run 5 nodes you won't be able to run 
> - You are encouraged to use python, but I'm not going to make it a strict requirement. The instructor and TA's ability to help with C or Rust, etc will be diminished in other languages.

### 1.1 Game Overview

* **Chosen Game:** Tic-Tac-Toe
* **Player Capacity:** 2 Players (Simulated via 2 CML Client nodes)

**Game Summary:** A classic turn-based strategy game played on a 3x3 grid. Two networked clients connect to a central game server. Players alternate turns placing their assigned symbol ('X' or 'O') into empty cells with the goal of securing three matching marks in a row horizontally, vertically, or diagonally.

1.2 Core Game Rules \& Win/Draw Conditions

* **Turn Mechanics:** Player 1 (assigned symbol 'X') connects first and moves first. Player 2 (assigned symbol 'O') connects second. The server strictly enforces turn order by ignoring or rejecting inputs sent out of turn. Players send coordinate inputs (e.g., row and column) during their active turn. Invalid inputs or attempts to overwrite an occupied cell prompt an error response from the server without forfeiting the player's turn.
* **Victory Condition:** A player wins immediately upon placing three of their symbols in a continuous row—either horizontally, vertically, or diagonally—across the 3x3 grid.
* **Draw/Tie Condition:** A draw is declared if all 9 grid cells are filled without either player completing a line of three symbols. If a client disconnects unexpectedly, the remaining connected player is awarded a win by forfeit.

\---

